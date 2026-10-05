(function () {
  'use strict';
  var el = App.el, canManage = App.is('Administrator', 'Fleet Manager');
  var form = document.getElementById('trip-form');
  var fStatus = document.getElementById('f-status'), fVehicle = document.getElementById('f-vehicle');
  var fFrom = document.getElementById('f-from'), fTo = document.getElementById('f-to');
  var editing = null;

  var columns = [
    { label: '#', render: function (t) { return t.trip_id; } },
    { label: 'Vehicle', render: function (t) { return el('strong', null, t.registration_no); } },
    { label: 'Driver', render: function (t) { return t.driver_name; } },
    { label: 'Route', render: function (t) { return t.origin + ' → ' + t.destination; } },
    { label: 'Dates', render: function (t) { return t.start_date === t.end_date ? t.start_date : t.start_date + ' to ' + t.end_date; } },
    { label: 'Distance (km)', num: true, render: function (t) { return App.fmt.num(t.distance, 1); } },
    { label: 'Status', render: function (t) { return App.badge(t.status); } }
  ];
  if (canManage) columns.push({ label: '', class: 'actions', render: function (t) {
    function btn(text, kind, fn) { return el('button', { type: 'button', class: 'btn btn-sm btn-outline-' + kind, onclick: fn }, text); }
    if (t.status === 'Planned') return [btn('Start', 'primary', function () { setStatus(t, 'In Progress'); }), btn('Edit', 'secondary', function () { openEdit(t); }),
      btn('Cancel', 'danger', function () { setStatus(t, 'Cancelled'); })];
    if (t.status === 'In Progress') return [btn('Complete', 'success', function () { setStatus(t, 'Completed'); }), btn('Cancel', 'danger', function () { setStatus(t, 'Cancelled'); })];
    return null;
  } });

  var list = App.createList({
    url: '/api/trips', tableEl: document.getElementById('list'), pagerEl: document.getElementById('pager'), columns: columns,
    empty: 'No trips match these filters.',
    params: function () { return { status: fStatus.value, vehicle_id: fVehicle.value, from: fFrom.value, to: fTo.value }; }
  });
  [fStatus, fVehicle, fFrom, fTo].forEach(function (n) { n.addEventListener('change', function () { list.reset(); }); });

  async function setStatus(t, status) {
    if (status === 'Cancelled' && !confirm('Cancel trip #' + t.trip_id + '?')) return;
    try { await App.api('PUT', '/api/trips/' + t.trip_id, { status: status }); App.toast('Trip #' + t.trip_id + ' is now ' + status); list.reload(); }
    catch (err) { App.toast(err.message, 'danger'); }
  }

  var editForm = null;
  if (canManage) {
    editForm = document.querySelector('#edit-modal form');
    App.handleSubmit(editForm, async function (values) {
      await App.api('PUT', '/api/trips/' + editing.trip_id, values);
      App.modal('edit-modal').hide(); App.toast('Trip #' + editing.trip_id + ' updated'); list.reload();
    });
  }
  function openEdit(t) {
    editing = t; App.clearErrors(editForm); editForm.reset();
    document.getElementById('edit-modal-title').textContent = 'Edit trip #' + t.trip_id;
    ['vehicle_id', 'driver_id', 'origin', 'destination', 'distance', 'start_date', 'end_date'].forEach(function (k) { editForm.elements[k].value = t[k]; });
    App.modal('edit-modal').show();
  }

  App.handleSubmit(form, async function (values) {
    var t = await App.api('POST', '/api/trips', values);
    App.toast('Trip #' + t.trip_id + ' created for ' + t.registration_no);
    ['origin', 'destination', 'distance', 'start_date', 'end_date'].forEach(function (k) { form.elements[k].value = ''; });
    list.reset();
  });

  async function loadChoices() {
    var results = await Promise.all([App.loadVehicles(true), App.api('GET', '/api/drivers/lookup')]);
    var vehicles = results[0], drivers = results[1].items;
    var usable = vehicles.filter(function (v) { return v.status !== 'Inactive'; });
    var vLabel = function (v) { return v.registration_no + ' (' + v.type + (v.status === 'Under Maintenance' ? ', in service' : '') + ')'; };
    App.fillSelect(form.elements.vehicle_id, usable, 'vehicle_id', vLabel, 'Choose a vehicle');
    App.fillSelect(form.elements.driver_id, drivers, 'id', function (d) { return d.name; }, 'Choose a driver');
    App.fillSelect(fVehicle, vehicles, 'vehicle_id', function (v) { return v.registration_no; }, 'All vehicles');
    if (editForm) {
      App.fillSelect(editForm.elements.vehicle_id, vehicles, 'vehicle_id', vLabel);
      App.fillSelect(editForm.elements.driver_id, drivers, 'id', function (d) { return d.name; });
    }
  }
  loadChoices().catch(function (err) { App.toast(err.message, 'danger'); });
  list.reload();
})();
