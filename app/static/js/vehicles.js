(function () {
  'use strict';
  var el = App.el, isAdmin = App.is('Administrator');
  var q = document.getElementById('q'), fStatus = document.getElementById('f-status'), fType = document.getElementById('f-type');
  var editing = null;   // vehicle being edited, or null when adding
  var openForm = function () {};   // set below for administrators

  var columns = [
    { label: 'Registration', render: function (v) { return el('strong', null, v.registration_no); } },
    { label: 'Type', render: function (v) { return v.type; } },
    { label: 'Make / model', render: function (v) { return v.make + ' ' + v.model; } },
    { label: 'Year', render: function (v) { return v.year; } },
    { label: 'Fuel', render: function (v) { return v.fuel_type; } },
    { label: 'Odometer (km)', num: true, render: function (v) { return App.fmt.num(v.odometer, 1); } },
    { label: 'Status', render: function (v) { return App.badge(v.status); } }
  ];
  if (isAdmin) columns.push({ label: '', class: 'actions', render: function (v) {
    return [el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm', onclick: function () { openForm(v); } }, 'Edit'),
      v.status === 'Inactive'
        ? el('button', { type: 'button', class: 'btn btn-outline-success btn-sm', onclick: function () { change(v, 'POST', '/reactivate', 'reactivated'); } }, 'Reactivate')
        : el('button', { type: 'button', class: 'btn btn-outline-danger btn-sm', onclick: function () { change(v, 'DELETE', '', 'deactivated'); } }, 'Deactivate')];
  } });

  var list = App.createList({
    url: '/api/vehicles', tableEl: document.getElementById('list'), pagerEl: document.getElementById('pager'), columns: columns,
    empty: 'No vehicles match these filters.',
    params: function () { return { q: q.value.trim(), status: fStatus.value, type: fType.value }; }
  });
  q.addEventListener('input', App.debounce(function () { list.reset(); }, 300));
  [fStatus, fType].forEach(function (n) { n.addEventListener('change', function () { list.reset(); }); });

  async function change(v, method, suffix, verb) {
    if (method === 'DELETE' && !confirm('Deactivate ' + v.registration_no + '? The vehicle stays in the system as Inactive.')) return;
    try { await App.api(method, '/api/vehicles/' + v.vehicle_id + suffix); App.toast(v.registration_no + ' ' + verb); list.reload(); }
    catch (err) { App.toast(err.message, 'danger'); }
  }

  if (isAdmin) {
    var modalEl = document.getElementById('vehicle-modal'), form = modalEl.querySelector('form');
    openForm = function (v) {
      editing = v || null;
      App.clearErrors(form); form.reset();
      document.getElementById('vehicle-modal-title').textContent = v ? 'Edit ' + v.registration_no : 'Add vehicle';
      document.getElementById('odometer-group').classList.toggle('d-none', !!v);
      document.getElementById('v-odometer').disabled = !!v;
      if (v) ['registration_no', 'type', 'make', 'model', 'year', 'fuel_type'].forEach(function (k) { form.elements[k].value = v[k]; });
      App.modal('vehicle-modal').show();
    };
    document.getElementById('add').addEventListener('click', function () { openForm(null); });
    App.handleSubmit(form, async function (values) {
      if (editing) await App.api('PUT', '/api/vehicles/' + editing.vehicle_id, values);
      else await App.api('POST', '/api/vehicles', values);
      App.modal('vehicle-modal').hide();
      App.toast(editing ? 'Vehicle updated' : 'Vehicle added');
      list.reload();
    });
  }
  list.reload();
})();
