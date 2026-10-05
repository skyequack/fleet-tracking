(function () {
  'use strict';
  var fmt = App.fmt;
  var form = document.getElementById('fuel-form');
  var fVehicle = document.getElementById('f-vehicle-filter'), fFrom = document.getElementById('f-from'), fTo = document.getElementById('f-to');

  var list = App.createList({
    url: '/api/fuel', tableEl: document.getElementById('list'), pagerEl: document.getElementById('pager'),
    empty: App.is('Operator') ? 'You have not recorded any fills yet.' : 'No fuel records match these filters.',
    params: function () { return { vehicle_id: fVehicle.value, from: fFrom.value, to: fTo.value }; },
    columns: [
      { label: 'Date', render: function (r) { return r.date; } },
      { label: 'Vehicle', render: function (r) { return App.el('strong', null, r.registration_no); } },
      { label: 'Odometer (km)', num: true, render: function (r) { return fmt.num(r.odometer, 1); } },
      { label: 'Litres', num: true, render: function (r) { return fmt.num(r.quantity, 2); } },
      { label: 'SAR/L', num: true, render: function (r) { return fmt.money(r.price_per_litre); } },
      { label: 'Total (SAR)', num: true, render: function (r) { return fmt.money(r.total_cost); } },
      { label: 'Tank', render: function (r) { return r.full_tank ? 'Full' : 'Partial'; } },
      { label: 'km/L', num: true, render: function (r) { return fmt.num(r.km_per_l, 2); } }
    ]
  });
  [fVehicle, fFrom, fTo].forEach(function (n) { n.addEventListener('change', function () { list.reset(); }); });

  if (form) {
    form.elements.date.value = App.today();
    App.handleSubmit(form, async function (values) {
      var r = await App.api('POST', '/api/fuel', values);
      App.toast('Saved: ' + fmt.num(r.quantity, 2) + ' L, SAR ' + fmt.money(r.total_cost) + (r.km_per_l ? ', ' + fmt.num(r.km_per_l, 2) + ' km/L' : ''));
      ['odometer', 'quantity', 'price_per_litre'].forEach(function (k) { form.elements[k].value = ''; });
      list.reset(); drawCharts();
    });
  }

  var chartsEl = document.getElementById('charts');
  async function drawCharts() {
    if (!chartsEl) return;
    var d;
    try { d = await App.api('GET', '/api/analytics/fuel'); } catch (err) { App.banner(chartsEl, err.message, 'danger'); return; }
    App.clear(chartsEl);
    function cell() { var n = App.el('div', { class: 'col-12 col-xl-6' }); chartsEl.appendChild(n); return n; }
    var months = d.monthly.map(function (m) { return m.month; });
    var p1 = Charts.card(cell(), { title: 'Fuel cost by month', sub: 'SAR per month, ' + d.window.from + ' to ' + d.window.to,
      columns: [{ label: 'Month', render: function (m) { return m.month; } }, { label: 'Litres', num: true, render: function (m) { return fmt.num(m.fuel_litres); } },
        { label: 'Cost (SAR)', num: true, render: function (m) { return fmt.money(m.fuel_cost); } }], rows: d.monthly });
    Charts.bars(p1, [{ name: 'Fuel cost (SAR)', x: months, y: d.monthly.map(function (m) { return m.fuel_cost; }), color: 1 }], { yTitle: 'SAR', dp: 0 });
    var p2 = Charts.card(cell(), { title: 'Fuel efficiency by vehicle type', sub: 'Kilometres per litre',
      columns: [{ label: 'Type', render: function (t) { return t.type; } }, { label: 'Litres', num: true, render: function (t) { return fmt.num(t.fuel_litres); } },
        { label: 'km/L', num: true, render: function (t) { return fmt.num(t.km_per_l, 2); } }], rows: d.by_type });
    Charts.bars(p2, [{ name: 'km/L', x: d.by_type.map(function (t) { return t.type; }), y: d.by_type.map(function (t) { return t.km_per_l; }), color: 1 }],
      { yTitle: 'km per litre', dp: 2 });
  }

  App.loadVehicles().then(function (vehicles) {
    App.fillSelect(fVehicle, vehicles, 'vehicle_id', function (v) { return v.registration_no; }, 'All vehicles');
    if (form) App.fillSelect(form.elements.vehicle_id, vehicles.filter(function (v) { return v.status !== 'Inactive'; }), 'vehicle_id',
      function (v) { return v.registration_no + ' (' + v.fuel_type + ')'; }, 'Choose a vehicle');
  }).catch(function (err) { App.toast(err.message, 'danger'); });
  list.reload(); drawCharts();
})();
