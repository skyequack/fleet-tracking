(function () {
  'use strict';
  var el = App.el, fmt = App.fmt, canEdit = App.is('Administrator', 'Operator');
  var form = document.getElementById('record-form');
  var fStatus = document.getElementById('f-status'), fType = document.getElementById('f-type'), fVehicle = document.getElementById('f-vehicle');
  var advancing = null, partFor = null;

  function parts(m) {
    if (!m.parts.length) return el('span', { class: 'text-body-secondary' }, 'No parts');
    return el('details', null, el('summary', null, m.parts.length + ' part' + (m.parts.length === 1 ? '' : 's')),
      el('ul', { class: 'mb-0 ps-3 small' }, m.parts.map(function (p) { return el('li', null, p.quantity + ' × ' + p.part_name + ' @ ' + fmt.money(p.unit_cost)); })));
  }
  var vehicleNames = {};
  var columns = [
    { label: 'Date', render: function (m) { return m.service_date; } },
    { label: 'Vehicle', render: function (m) { return el('strong', null, vehicleNames[m.vehicle_id] || ('#' + m.vehicle_id)); } },
    { label: 'Service', render: function (m) { return m.service_type; } },
    { label: 'Status', render: function (m) { return App.badge(m.status); } },
    { label: 'Odometer (km)', num: true, render: function (m) { return fmt.num(m.odometer, 1); } },
    { label: 'Cost (SAR)', num: true, render: function (m) { return fmt.money(m.cost); } },
    { label: 'Parts', render: parts },
    { label: 'Next service', render: function (m) { return m.next_service_date || '–'; } }
  ];
  if (canEdit) columns.push({ label: '', class: 'actions', render: function (m) {
    function btn(text, kind, fn) { return el('button', { type: 'button', class: 'btn btn-sm btn-outline-' + kind, onclick: fn }, text); }
    var out = [];
    if (m.status === 'Scheduled') out.push(btn('Start', 'primary', function () { openAdvance(m, 'In Progress'); }));
    if (m.status === 'In Progress') out.push(btn('Complete', 'success', function () { openAdvance(m, 'Completed'); }));
    if (m.status !== 'Completed') out.push(btn('Add part', 'secondary', function () { openPart(m); }));
    return out;
  } });

  var list = App.createList({
    url: '/api/maintenance', tableEl: document.getElementById('list'), pagerEl: document.getElementById('pager'), columns: columns,
    empty: 'No services match these filters.',
    params: function () { return { status: fStatus.value, service_type: fType.value, vehicle_id: fVehicle.value }; }
  });
  [fStatus, fType, fVehicle].forEach(function (n) { n.addEventListener('change', function () { list.reset(); }); });

  async function loadUpcoming() {
    var d;
    try { d = await App.api('GET', '/api/maintenance/upcoming'); }
    catch (err) { App.banner(document.getElementById('scheduled'), err.message, 'danger'); return; }
    App.renderTable(document.getElementById('scheduled'), [
      { label: 'Date', render: function (s) { return s.service_date; } },
      { label: 'Vehicle', render: function (s) { return el('strong', null, s.registration_no); } },
      { label: 'Service', render: function (s) { return s.service_type; } }], d.scheduled, 'Nothing is scheduled.');
    App.renderTable(document.getElementById('due'), [
      { label: 'Due', render: function (s) { return [s.next_service_date, ' ', s.overdue ? App.badge('Overdue', 'danger') : null]; } },
      { label: 'Vehicle', render: function (s) { return el('strong', null, s.registration_no); } },
      { label: 'Service', render: function (s) { return s.service_type; } }], d.due, 'Nothing is due in the next 30 days.');
  }
  function refresh() { list.reload(); loadUpcoming(); }

  if (canEdit) {
    var advForm = document.querySelector('#advance-modal form'), partForm = document.querySelector('#part-modal form');
    var openAdvance = function (m, status) {
      advancing = { record: m, status: status }; App.clearErrors(advForm); advForm.reset();
      document.getElementById('advance-modal-title').textContent = (status === 'In Progress' ? 'Start service' : 'Complete service') + ' #' + m.maintenance_id;
      document.getElementById('advance-text').textContent = m.service_type + ' on ' + (vehicleNames[m.vehicle_id] || ('vehicle #' + m.vehicle_id)) + '.';
      advForm.elements.cost.value = status === 'Completed' ? m.cost : '';
      advForm.elements.technician.value = m.technician || '';
      App.modal('advance-modal').show();
    };
    App.handleSubmit(advForm, async function (values) {
      var body = Object.assign({ status: advancing.status }, values);
      var r = await App.api('PUT', '/api/maintenance/' + advancing.record.maintenance_id + '/status', body);
      App.modal('advance-modal').hide();
      App.toast('Service is now ' + r.record.status + '; vehicle status: ' + r.vehicle_status);
      var box = document.getElementById('warnings');
      if (r.warnings.length) App.banner(box, 'Planned trips overlap this service: ' + r.warnings.map(function (w) { return '#' + w.trip_id + ' (' + w.start_date + ' to ' + w.end_date + ')'; }).join(', ') + '. They are not blocked; reschedule them if needed.', 'warning');
      else App.clear(box);
      App.loadVehicles(true);
      refresh();
    });
    var openPart = function (m) { partFor = m; App.clearErrors(partForm); partForm.reset();
      document.getElementById('part-modal-title').textContent = 'Add a part to service #' + m.maintenance_id; App.modal('part-modal').show(); };
    App.handleSubmit(partForm, async function (values) {
      await App.api('POST', '/api/maintenance/' + partFor.maintenance_id + '/parts', values);
      App.modal('part-modal').hide(); App.toast('Part added'); list.reload();
    });
  }

  if (form) {
    form.elements.service_date.value = App.today();
    App.handleSubmit(form, async function (values) {
      await App.api('POST', '/api/maintenance', values);
      App.toast('Service scheduled'); form.elements.cost.value = ''; form.elements.technician.value = ''; refresh();
    });
  }

  App.loadVehicles().then(function (vehicles) {
    vehicles.forEach(function (v) { vehicleNames[v.vehicle_id] = v.registration_no; });
    App.fillSelect(fVehicle, vehicles, 'vehicle_id', function (v) { return v.registration_no; }, 'All vehicles');
    if (form) App.fillSelect(form.elements.vehicle_id, vehicles.filter(function (v) { return v.status !== 'Inactive'; }), 'vehicle_id',
      function (v) { return v.registration_no + ' (' + v.type + ')'; }, 'Choose a vehicle');
    refresh();
  }).catch(function (err) { App.toast(err.message, 'danger'); });
})();
