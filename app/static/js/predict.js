// Predictive Maintenance screen: GET /api/predict. When the model file is missing or fails its checks the API
// answers 503 and this page says so; the rest of the app is unaffected.
(function () {
  'use strict';
  var el = App.el, fmt = App.fmt;
  var RISK = { LOW: 'success', MEDIUM: 'warning', HIGH: 'danger' };
  var data = null;
  var fRisk = document.getElementById('f-risk');

  function pct(p) { return Math.round(p * 100) + '%'; }

  function renderList() {
    var rows = data.items.filter(function (r) { return !fRisk.value || r.risk === fRisk.value; });
    App.renderTable(document.getElementById('list'), [
      { label: 'Vehicle', render: function (r) { return el('strong', null, r.registration_no); } },
      { label: 'Type', render: function (r) { return r.type; } },
      { label: 'Risk', render: function (r) { return App.badge(r.risk, RISK[r.risk]); } },
      { label: 'P(HIGH)', num: true, render: function (r) { return pct(r.p_high); } },
      { label: '', class: 'w-25', render: function (r) {
        return el('div', { class: 'progress risk-bar', role: 'img', 'aria-label': 'Probability of HIGH risk ' + pct(r.p_high) },
          el('div', { class: 'progress-bar bg-' + RISK[r.risk], style: 'width:' + pct(r.p_high) }));
      } },
      { label: 'Note', render: function (r) { return r.imputed_fuel ? 'Fuel efficiency estimated from the vehicle type' : ''; } }
    ], rows, 'No vehicles in this class.');
  }

  async function load() {
    try { data = await App.api('GET', '/api/predict'); }
    catch (err) {
      App.banner(document.getElementById('status'), err.status === 503
        ? 'The prediction model is not available. ' + err.message.replace(/^model_unavailable: /, '') : err.message, err.status === 503 ? 'info' : 'danger');
      return;
    }
    document.getElementById('label-source').textContent = data.model.label_source || '';
    document.getElementById('as-of').textContent = 'Scored as of ' + data.as_of + '; vehicles that are not Inactive';
    document.getElementById('model-info').textContent = 'Random Forest trained ' + (data.model.trained_at || '').slice(0, 10) +
      '. Accuracy on the ' + data.model.test_rows + ' held-out snapshots: ' + (data.model.accuracy * 100).toFixed(1) + '%' +
      (data.model.grouped_accuracy !== null ? ' (' + (data.model.grouped_accuracy * 100).toFixed(1) + '% when whole vehicles are held out).' : '.');

    var summary = document.getElementById('summary');
    App.clear(summary);
    ['HIGH', 'MEDIUM', 'LOW'].forEach(function (c) {
      summary.appendChild(el('div', { class: 'col-4 col-md-2' }, el('div', { class: 'card stat-card h-100' }, el('div', { class: 'card-body py-3' },
        el('div', { class: 'stat-value' }, fmt.num(data.summary[c])), el('div', { class: 'stat-label' }, App.badge(c, RISK[c]), ' vehicles')))));
    });

    var counts = ['LOW', 'MEDIUM', 'HIGH'].map(function (c) { return { risk: c, vehicles: data.summary[c] }; });
    var plot = Charts.card(document.getElementById('chart'), { title: 'Risk distribution', sub: 'Number of vehicles in each class today',
      columns: [{ label: 'Risk class', render: function (r) { return r.risk; } }, { label: 'Vehicles', num: true, render: function (r) { return r.vehicles; } }], rows: counts });
    Charts.bars(plot, [{ name: 'Vehicles', x: counts.map(function (r) { return r.risk; }), y: counts.map(function (r) { return r.vehicles; }), color: 1 }],
      { yTitle: 'Vehicles', dp: 0 });
    renderList();
  }
  fRisk.addEventListener('change', function () { if (data) renderList(); });
  load();
})();
