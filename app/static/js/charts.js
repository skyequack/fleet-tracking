// Plotly helpers. Colours come from the CSS tokens in app.css so charts follow light/dark mode (reference palette:
// blue = series 1, orange = series 2). One axis per chart, thin marks, a 2px surface gap between bars, a legend
// whenever there are two or more series, and a table of the same numbers under every chart.
(function () {
  'use strict';
  var el = App.el;
  var drawn = [];   // {node, build} so charts can be redrawn when the colour scheme changes

  function token(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function layout(opts) {
    var text = token('--viz-text-2'), grid = token('--viz-grid'), axis = token('--viz-axis'), surface = token('--viz-surface');
    var base = {
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: text },
      margin: { l: opts.marginLeft || 56, r: 12, t: opts.legend ? 36 : 12, b: opts.marginBottom || 48 },
      bargap: 0.4, barmode: opts.barmode || 'group', hovermode: 'closest',
      xaxis: { gridcolor: 'rgba(0,0,0,0)', linecolor: axis, zerolinecolor: axis, automargin: true, title: opts.xTitle ? { text: opts.xTitle } : undefined,
        tickangle: opts.tickangle },
      yaxis: { gridcolor: grid, linecolor: 'rgba(0,0,0,0)', zerolinecolor: axis, automargin: true, rangemode: 'tozero',
        title: opts.yTitle ? { text: opts.yTitle } : undefined },
      showlegend: !!opts.legend,
      legend: { orientation: 'h', x: 0, y: 1.14, font: { color: text } }
    };
    if (opts.horizontal) {
      base.xaxis.gridcolor = grid; base.xaxis.rangemode = 'tozero'; base.yaxis.gridcolor = 'rgba(0,0,0,0)';
      base.yaxis.autorange = 'reversed'; base.yaxis.rangemode = 'normal';
    }
    base._surface = surface;
    return base;
  }

  // series: [{name, x, y, color: 1|2}] ; opts: {legend, horizontal, yTitle, xTitle, barmode, hover, ...}
  function bars(node, series, opts) {
    opts = opts || {};
    function build() {
      var l = layout(Object.assign({}, opts, { legend: series.length > 1 }));
      var surface = l._surface; delete l._surface;
      var traces = series.map(function (s) {
        return {
          type: 'bar', name: s.name, orientation: opts.horizontal ? 'h' : 'v',
          x: opts.horizontal ? s.y : s.x, y: opts.horizontal ? s.x : s.y,
          marker: { color: token('--viz-s' + (s.color || 1)), line: { color: surface, width: 2 } },
          hovertemplate: opts.hover || ('%{' + (opts.horizontal ? 'y' : 'x') + '}<br>' + s.name + ': %{' + (opts.horizontal ? 'x' : 'y') + ':,.' + (opts.dp === undefined ? 1 : opts.dp) + 'f}<extra></extra>')
        };
      });
      Plotly.react(node, traces, l, { displayModeBar: false, responsive: true });
    }
    build();
    drawn.push({ node: node, build: build });
  }

  document.addEventListener('themechange', function () {
    drawn = drawn.filter(function (d) { return document.body.contains(d.node); });
    drawn.forEach(function (d) { d.build(); });
  });

  // A titled card holding the chart and, under it, the same numbers as a table.
  // spec: {title, sub, columns: [{label, render, num}], rows}
  function card(container, spec) {
    var plot = el('div', { class: 'chart', role: 'img', 'aria-label': spec.title });
    var tableHolder = el('div');
    var node = el('div', { class: 'chart-card h-100' },
      el('h2', null, spec.title), spec.sub ? el('div', { class: 'chart-sub' }, spec.sub) : null, plot,
      el('details', null, el('summary', null, 'View as table'), tableHolder));
    container.appendChild(node);
    App.renderTable(tableHolder, spec.columns, spec.rows, 'No data in this window.');
    return plot;
  }

  window.Charts = { bars: bars, card: card };
})();
