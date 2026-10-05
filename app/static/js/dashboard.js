(function () {
  'use strict';
  var el = App.el, fmt = App.fmt;
  var form = document.getElementById('window-form');

  function stat(label, value, unit) {
    return el('div', { class: 'card stat-card h-100' }, el('div', { class: 'card-body py-3' },
      el('div', { class: 'stat-value' }, value, unit ? el('span', { class: 'fs-6 fw-normal text-body-secondary' }, ' ' + unit) : null),
      el('div', { class: 'stat-label' }, label)));
  }

  function monthLabel(m) { return m; }

  async function load() {
    var params = { from: form.from.value, to: form.to.value };
    var d;
    try { d = await App.api('GET', '/api/dashboard' + App.qs(params)); }
    catch (err) { App.banner(document.getElementById('error'), err.message, 'danger'); return; }
    App.clear(document.getElementById('error'));
    document.getElementById('window-label').textContent = d.window.from + ' to ' + d.window.to + ' (' + d.window.days + ' days)';

    var c = d.cards, s = d.strip;
    var cards = document.getElementById('cards');
    App.clear(cards);
    [['Total vehicles', fmt.num(c.total_vehicles)], ['Active', fmt.num(c.active)], ['Under maintenance', fmt.num(c.under_maintenance)],
      ['Total distance', fmt.num(c.total_distance_km), 'km'], ['Fuel cost', fmt.num(c.fuel_cost), 'SAR'],
      ['Maintenance cost', fmt.num(c.maintenance_cost), 'SAR']].forEach(function (x) {
      cards.appendChild(el('div', { class: 'col-6 col-md-4 col-xl-2' }, stat(x[0], x[1], x[2])));
    });
    var strip = document.getElementById('strip');
    App.clear(strip);
    [['Fuel consumed', fmt.num(s.fuel_litres), 'L'], ['Fuel efficiency', fmt.num(s.fuel_efficiency_km_per_l, 2), 'km/L'],
      ['Cost per km', fmt.num(s.cost_per_km, 3), 'SAR/km'], ['Utilisation', fmt.pct(s.utilisation_pct)]].forEach(function (x) {
      strip.appendChild(stat(x[0], x[1], x[2]));
    });

    var charts = document.getElementById('charts');
    App.clear(charts);
    function cell() { var n = el('div', { class: 'col-12 col-xl-6' }); charts.appendChild(n); return n; }
    var ser = d.series;
    var months = ser.monthly_fuel.map(function (r) { return monthLabel(r.month); });

    var p1 = Charts.card(cell(), { title: 'Monthly fuel consumption', sub: 'Litres per month',
      columns: [{ label: 'Month', render: function (r) { return r.month; } }, { label: 'Litres', num: true, render: function (r) { return fmt.num(r.fuel_litres); } }],
      rows: ser.monthly_fuel });
    Charts.bars(p1, [{ name: 'Fuel (L)', x: months, y: ser.monthly_fuel.map(function (r) { return r.fuel_litres; }), color: 1 }],
      { yTitle: 'Litres', dp: 0 });

    var p2 = Charts.card(cell(), { title: 'Fuel cost vs maintenance cost', sub: 'SAR per month',
      columns: [{ label: 'Month', render: function (r) { return r.month; } }, { label: 'Fuel (SAR)', num: true, render: function (r) { return fmt.money(r.fuel_cost); } },
        { label: 'Maintenance (SAR)', num: true, render: function (r) { return fmt.money(r.maintenance_cost); } }],
      rows: ser.monthly_cost });
    Charts.bars(p2, [
      { name: 'Fuel', x: months, y: ser.monthly_cost.map(function (r) { return r.fuel_cost; }), color: 1 },
      { name: 'Maintenance', x: months, y: ser.monthly_cost.map(function (r) { return r.maintenance_cost; }), color: 2 }],
      { yTitle: 'SAR', dp: 0 });

    var p3 = Charts.card(cell(), { title: 'Fuel efficiency by vehicle type', sub: 'Kilometres per litre (distance / fuel)',
      columns: [{ label: 'Type', render: function (r) { return r.type; } }, { label: 'km/L', num: true, render: function (r) { return fmt.num(r.km_per_l, 2); } }],
      rows: ser.efficiency_by_type });
    Charts.bars(p3, [{ name: 'km/L', x: ser.efficiency_by_type.map(function (r) { return r.type; }), y: ser.efficiency_by_type.map(function (r) { return r.km_per_l; }), color: 1 }],
      { yTitle: 'km per litre', dp: 2 });

    var top = ser.top_cost_per_km;
    var p4 = Charts.card(cell(), { title: 'Top 10 vehicles by cost per km', sub: '(Fuel cost + maintenance cost) / distance, SAR per km',
      columns: [{ label: 'Vehicle', render: function (r) { return r.registration_no; } }, { label: 'Type', render: function (r) { return r.type; } },
        { label: 'SAR/km', num: true, render: function (r) { return fmt.num(r.cost_per_km, 3); } }],
      rows: top });
    Charts.bars(p4, [{ name: 'SAR/km', x: top.map(function (r) { return r.registration_no; }), y: top.map(function (r) { return r.cost_per_km; }), color: 1 }],
      { horizontal: true, xTitle: 'SAR per km', dp: 3, marginLeft: 80 });
  }

  form.addEventListener('submit', function (ev) { ev.preventDefault(); load(); });
  document.getElementById('reset').addEventListener('click', function () { form.from.value = ''; form.to.value = ''; load(); });
  load();
})();
