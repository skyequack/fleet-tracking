(function () {
  'use strict';
  var fmt = App.fmt, el = App.el;
  var form = document.getElementById('window-form');

  async function load() {
    var p = App.qs({ from: form.from.value, to: form.to.value });
    var res;
    try {
      res = await Promise.all(['cost-per-km', 'utilisation', 'efficiency'].map(function (n) { return App.api('GET', '/api/analytics/' + n + p); }));
    } catch (err) { App.banner(document.getElementById('error'), err.message, 'danger'); return; }
    App.clear(document.getElementById('error'));
    var cost = res[0], util = res[1], eff = res[2];
    document.getElementById('window-label').textContent = cost.window.from + ' to ' + cost.window.to + ' (' + cost.window.days + ' days)';

    // one table with every indicator by type
    var util_by = {}, eff_by = {};
    util.by_type.forEach(function (t) { util_by[t.type] = t; });
    eff.by_type.forEach(function (t) { eff_by[t.type] = t; });
    var rows = cost.by_type.map(function (t) { return { type: t.type, vehicles: t.vehicles, distance: t.distance_km, litres: eff_by[t.type].fuel_litres,
      km_per_l: eff_by[t.type].km_per_l, cost_per_km: t.cost_per_km, util: util_by[t.type].utilisation_pct }; });
    rows.push({ type: 'Fleet', vehicles: rows.reduce(function (a, r) { return a + r.vehicles; }, 0), distance: rows.reduce(function (a, r) { return a + r.distance; }, 0),
      litres: rows.reduce(function (a, r) { return a + r.litres; }, 0), km_per_l: eff.fleet_km_per_l, cost_per_km: cost.fleet_cost_per_km, util: util.fleet_utilisation_pct });
    App.renderTable(document.getElementById('by-type'), [
      { label: 'Type', render: function (r) { return r.type === 'Fleet' ? el('strong', null, 'Fleet') : r.type; } },
      { label: 'Vehicles', num: true, render: function (r) { return r.vehicles; } },
      { label: 'Distance (km)', num: true, render: function (r) { return fmt.num(r.distance); } },
      { label: 'Fuel (L)', num: true, render: function (r) { return fmt.num(r.litres); } },
      { label: 'Efficiency (km/L)', num: true, render: function (r) { return fmt.num(r.km_per_l, 2); } },
      { label: 'Cost per km (SAR)', num: true, render: function (r) { return fmt.num(r.cost_per_km, 3); } },
      { label: 'Utilisation', num: true, render: function (r) { return fmt.pct(r.util); } }], rows);

    var charts = document.getElementById('charts');
    App.clear(charts);
    function cell() { var n = el('div', { class: 'col-12 col-xl-6' }); charts.appendChild(n); return n; }

    var worst = cost.vehicles.filter(function (v) { return v.cost_per_km !== null; }).slice(0, 15);
    var c1 = Charts.card(cell(), { title: 'Cost per km: highest 15 vehicles', sub: '(Fuel cost + maintenance cost) / distance, SAR per km',
      columns: [{ label: 'Vehicle', render: function (v) { return v.registration_no; } }, { label: 'Type', render: function (v) { return v.type; } },
        { label: 'Distance (km)', num: true, render: function (v) { return fmt.num(v.distance); } }, { label: 'SAR/km', num: true, render: function (v) { return fmt.num(v.cost_per_km, 3); } }], rows: worst });
    Charts.bars(c1, [{ name: 'SAR/km', x: worst.map(function (v) { return v.registration_no; }), y: worst.map(function (v) { return v.cost_per_km; }), color: 1 }],
      { horizontal: true, xTitle: 'SAR per km', dp: 3, marginLeft: 80 });

    var live = util.vehicles.slice(0, 15);
    var c2 = Charts.card(cell(), { title: 'Utilisation: busiest 15 vehicles', sub: 'Active days as a share of the window (vehicles that are not Inactive)',
      columns: [{ label: 'Vehicle', render: function (v) { return v.registration_no; } }, { label: 'Type', render: function (v) { return v.type; } },
        { label: 'Active days', num: true, render: function (v) { return fmt.num(v.active_days); } }, { label: 'Utilisation', num: true, render: function (v) { return fmt.pct(v.utilisation_pct); } }], rows: live });
    Charts.bars(c2, [{ name: 'Utilisation (%)', x: live.map(function (v) { return v.registration_no; }), y: live.map(function (v) { return v.utilisation_pct; }), color: 1 }],
      { horizontal: true, xTitle: 'Utilisation (%)', dp: 1, marginLeft: 80 });

    var types = eff.by_type;
    var c3 = Charts.card(cell(), { title: 'Fuel efficiency by vehicle type', sub: 'Kilometres per litre (distance / fuel)',
      columns: [{ label: 'Type', render: function (t) { return t.type; } }, { label: 'Distance (km)', num: true, render: function (t) { return fmt.num(t.distance_km); } },
        { label: 'Fuel (L)', num: true, render: function (t) { return fmt.num(t.fuel_litres); } }, { label: 'km/L', num: true, render: function (t) { return fmt.num(t.km_per_l, 2); } }], rows: types });
    Charts.bars(c3, [{ name: 'km/L', x: types.map(function (t) { return t.type; }), y: types.map(function (t) { return t.km_per_l; }), color: 1 }], { yTitle: 'km per litre', dp: 2 });

    var ct = cost.by_type;
    var c4 = Charts.card(cell(), { title: 'Fuel cost and maintenance cost by vehicle type', sub: 'SAR in the window',
      columns: [{ label: 'Type', render: function (t) { return t.type; } }, { label: 'Fuel (SAR)', num: true, render: function (t) { return fmt.money(t.fuel_cost); } },
        { label: 'Maintenance (SAR)', num: true, render: function (t) { return fmt.money(t.maintenance_cost); } }], rows: ct });
    Charts.bars(c4, [{ name: 'Fuel', x: ct.map(function (t) { return t.type; }), y: ct.map(function (t) { return t.fuel_cost; }), color: 1 },
      { name: 'Maintenance', x: ct.map(function (t) { return t.type; }), y: ct.map(function (t) { return t.maintenance_cost; }), color: 2 }], { yTitle: 'SAR', dp: 0 });
  }

  form.addEventListener('submit', function (ev) { ev.preventDefault(); load(); });
  document.getElementById('reset').addEventListener('click', function () { form.from.value = ''; form.to.value = ''; load(); });
  load();
})();
