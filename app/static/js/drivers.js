(function () {
  'use strict';
  var el = App.el, isAdmin = App.is('Administrator');
  var q = document.getElementById('q'), fStatus = document.getElementById('f-status'), fWarn = document.getElementById('f-warning');
  var editing = null, openForm = function () {};

  function licence(d) {
    if (d.licence_expired) return App.badge('Expired', 'danger');
    if (d.licence_warning) return App.badge('Expiring soon', 'warning');
    return null;
  }
  var columns = [
    { label: 'Name', render: function (d) { return el('strong', null, d.name); } },
    { label: 'Phone', render: function (d) { return d.phone || '–'; } },
    { label: 'Licence no.', render: function (d) { return d.license_no; } },
    { label: 'Licence expiry', render: function (d) { return [d.license_expiry, ' ', licence(d)]; } },
    { label: 'Status', render: function (d) { return App.badge(d.status); } }
  ];
  if (isAdmin) columns.push({ label: '', class: 'actions', render: function (d) {
    return [el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm', onclick: function () { openForm(d); } }, 'Edit'),
      d.status === 'Inactive'
        ? el('button', { type: 'button', class: 'btn btn-outline-success btn-sm', onclick: function () { setStatus(d, 'Active'); } }, 'Reactivate')
        : el('button', { type: 'button', class: 'btn btn-outline-danger btn-sm', onclick: function () { deactivate(d); } }, 'Deactivate')];
  } });

  var list = App.createList({
    url: '/api/drivers', tableEl: document.getElementById('list'), pagerEl: document.getElementById('pager'), columns: columns,
    empty: 'No drivers match these filters.',
    params: function () { return { q: q.value.trim(), status: fStatus.value, licence_warning: fWarn.checked }; }
  });
  q.addEventListener('input', App.debounce(function () { list.reset(); }, 300));
  [fStatus, fWarn].forEach(function (n) { n.addEventListener('change', function () { list.reset(); }); });

  async function refreshBanner() {
    var box = document.getElementById('licence-banner');
    try {
      var r = await App.api('GET', '/api/drivers?licence_warning=1&per_page=1');
      if (r.total) App.banner(box, r.total + (r.total === 1 ? ' active driver has a licence that expires' : ' active drivers have licences that expire') + ' within 30 days.', 'warning');
      else App.clear(box);
    } catch (err) { App.clear(box); }
  }

  function refreshAll() { list.reload(); refreshBanner(); loadDrivers(); }

  async function setStatus(d, status) {
    try { await App.api('PUT', '/api/drivers/' + d.driver_id, { status: status }); App.toast(d.name + ' updated'); refreshAll(); }
    catch (err) { App.toast(err.message, 'danger'); }
  }
  async function deactivate(d) {
    if (!confirm('Deactivate ' + d.name + '? The driver stays in the system as Inactive.')) return;
    try { await App.api('DELETE', '/api/drivers/' + d.driver_id); App.toast(d.name + ' deactivated'); refreshAll(); }
    catch (err) { App.toast(err.message, 'danger'); }
  }

  if (isAdmin) {
    var form = document.querySelector('#driver-modal form');
    openForm = function (d) {
      editing = d || null; App.clearErrors(form); form.reset();
      document.getElementById('driver-modal-title').textContent = d ? 'Edit ' + d.name : 'Add driver';
      if (d) {
        form.elements.name.value = d.name; form.elements.phone.value = d.phone || '';
        form.elements.license_no.value = d.license_no; form.elements.license_expiry.value = d.license_expiry;
      }
      App.modal('driver-modal').show();
    };
    document.getElementById('add').addEventListener('click', function () { openForm(null); });
    App.handleSubmit(form, async function (values) {
      if (editing) await App.api('PUT', '/api/drivers/' + editing.driver_id, values);
      else await App.api('POST', '/api/drivers', values);
      App.modal('driver-modal').hide(); App.toast(editing ? 'Driver updated' : 'Driver added');
      refreshAll();
    });
  }

  // ---- assignments --------------------------------------------------------------------------------------------
  var aForm = document.getElementById('assign-form'), aVehicle = document.getElementById('af-vehicle');
  var ending = null;
  var aColumns = [
    { label: 'Vehicle', render: function (a) { return el('strong', null, a.registration_no); } },
    { label: 'Driver', render: function (a) { return a.driver_name; } },
    { label: 'From', render: function (a) { return a.start_date; } },
    { label: 'To', render: function (a) { return a.end_date || App.badge('Current', 'success'); } },
    { label: '', class: 'actions', render: function (a) {
      return a.end_date ? null : el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm', onclick: function () { openEnd(a); } }, 'End assignment');
    } }
  ];
  var aList = App.createList({
    url: '/api/assignments', tableEl: document.getElementById('a-list'), pagerEl: document.getElementById('a-pager'), columns: aColumns,
    empty: 'No assignments to show.', params: function () { return { vehicle_id: aVehicle.value }; }
  });
  aVehicle.addEventListener('change', function () { aList.reset(); });

  async function loadDrivers() {
    var d = await App.api('GET', '/api/drivers/lookup');
    App.fillSelect(aForm.elements.driver_id, d.items, 'id', function (x) { return x.name; }, 'Choose a driver');
  }
  async function loadVehicles() {
    var vehicles = await App.loadVehicles(true);
    App.fillSelect(aForm.elements.vehicle_id, vehicles.filter(function (v) { return v.status !== 'Inactive'; }), 'vehicle_id',
      function (v) { return v.registration_no + ' (' + v.type + ')'; }, 'Choose a vehicle');
    App.fillSelect(aVehicle, vehicles, 'vehicle_id', function (v) { return v.registration_no; }, 'All vehicles');
  }
  aForm.elements.start_date.value = App.today();
  App.handleSubmit(aForm, async function (values) {
    await App.api('POST', '/api/assignments', values);
    App.toast('Driver assigned'); aList.reload();
  });

  var endForm = document.querySelector('#end-modal form');
  function openEnd(a) {
    ending = a; App.clearErrors(endForm); endForm.reset(); endForm.elements.end_date.value = App.today();
    document.getElementById('end-modal-title').textContent = 'End assignment: ' + a.driver_name + ' on ' + a.registration_no;
    App.modal('end-modal').show();
  }
  App.handleSubmit(endForm, async function (values) {
    await App.api('PUT', '/api/assignments/' + ending.assignment_id, values);
    App.modal('end-modal').hide(); App.toast('Assignment ended'); aList.reload();
  });

  list.reload(); refreshBanner(); aList.reload();
  Promise.all([loadVehicles(), loadDrivers()]).catch(function (err) { App.toast(err.message, 'danger'); });
})();
