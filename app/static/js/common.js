// Shared helpers for every screen. User-supplied text only ever reaches the page through textContent
// (el() below); nothing in this app assigns innerHTML (ARCHITECTURE.md 11, XSS).
(function () {
  'use strict';

  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : '';
  var role = document.body.dataset.role || '';

  function ApiError(status, message, field) {
    this.status = status; this.message = message; this.field = field || null;
  }
  ApiError.prototype = Object.create(Error.prototype);

  async function api(method, url, body) {
    var opts = { method: method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (method !== 'GET') opts.headers['X-CSRF-Token'] = csrf;
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    var resp;
    try { resp = await fetch(url, opts); } catch (e) { throw new ApiError(0, 'Could not reach the server. Check the connection and try again.'); }
    var data = null;
    try { data = await resp.json(); } catch (e) { /* no body */ }
    if (resp.status === 401 && url.indexOf('/api/auth/login') !== 0) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
      throw new ApiError(401, 'Your session has ended. Please sign in again.');
    }
    if (!resp.ok) throw new ApiError(resp.status, (data && data.error) || resp.statusText, data && data.field);
    return data;
  }

  function qs(params) {
    var p = new URLSearchParams();
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v !== '' && v !== null && v !== undefined && v !== false) p.set(k, v === true ? '1' : v);
    });
    var s = p.toString();
    return s ? '?' + s : '';
  }

  // el('div', {class: 'x', onclick: fn, dataset: {a: 1}}, 'text', childNode, [more]) builds DOM without innerHTML.
  function el(tag, attrs) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') node.className = v;
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k.slice(0, 2) === 'on') node.addEventListener(k.slice(2), v);
      else if (v === true) node.setAttribute(k, '');
      else node.setAttribute(k, v);
    });
    function add(kid) {
      if (kid === null || kid === undefined || kid === false) return;
      if (Array.isArray(kid)) kid.forEach(add);
      else node.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    for (var i = 2; i < arguments.length; i++) add(arguments[i]);
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

  var formatters = {};
  function nf(dp) { return formatters[dp] || (formatters[dp] = new Intl.NumberFormat('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })); }
  var fmt = {
    num: function (n, dp) { return n === null || n === undefined ? '–' : nf(dp || 0).format(n); },
    money: function (n) { return n === null || n === undefined ? '–' : nf(2).format(n); },
    pct: function (n) { return n === null || n === undefined ? '–' : nf(1).format(n) + '%'; }
  };

  var BADGES = { 'Active': 'success', 'Under Maintenance': 'warning', 'Inactive': 'secondary', 'Planned': 'info',
    'In Progress': 'primary', 'Completed': 'success', 'Cancelled': 'secondary', 'Scheduled': 'info' };
  function badge(text, kind) { return el('span', { class: 'badge text-bg-' + (kind || BADGES[text] || 'secondary') }, text); }

  function toast(message, kind) {
    var holder = document.getElementById('toasts');
    var node = el('div', { class: 'toast align-items-center text-bg-' + (kind || 'success') + ' border-0', role: kind === 'danger' ? 'alert' : 'status', 'aria-live': 'polite' },
      el('div', { class: 'd-flex' }, el('div', { class: 'toast-body' }, message),
        el('button', { type: 'button', class: 'btn-close btn-close-white me-2 m-auto', 'data-bs-dismiss': 'toast', 'aria-label': 'Close' })));
    holder.appendChild(node);
    var t = new bootstrap.Toast(node, { delay: kind === 'danger' ? 8000 : 4000 });
    node.addEventListener('hidden.bs.toast', function () { node.remove(); });
    t.show();
  }

  function banner(container, message, kind) {
    clear(container).appendChild(el('div', { class: 'alert alert-' + (kind || 'info') + ' mb-3', role: 'alert' }, message));
  }

  // ---- forms: errors appear beside the offending field --------------------------------------------------------
  function clearErrors(form) {
    form.querySelectorAll('.is-invalid').forEach(function (n) { n.classList.remove('is-invalid'); });
    form.querySelectorAll('.invalid-feedback').forEach(function (n) { n.textContent = ''; });
    var summary = form.querySelector('[data-form-error]');
    if (summary) { summary.textContent = ''; summary.classList.add('d-none'); }
  }

  function showError(form, err) {
    var input = err.field && form.elements[err.field];
    if (input) {
      input.classList.add('is-invalid');
      var fb = input.parentElement.querySelector('.invalid-feedback');
      if (fb) { fb.textContent = err.message; return; }
    }
    var summary = form.querySelector('[data-form-error]');
    if (summary) { summary.textContent = err.message; summary.classList.remove('d-none'); }
    else toast(err.message, 'danger');
  }

  // Values for the API: data-type="number" -> Number, checkbox -> boolean, empty number/date/select -> left out.
  function readForm(form) {
    var out = {};
    Array.prototype.forEach.call(form.elements, function (f) {
      if (!f.name || f.disabled) return;
      if (f.type === 'checkbox') { out[f.name] = f.checked; return; }
      var v = f.value;
      if (f.dataset.type === 'number' || f.dataset.type === 'int') { if (v !== '') out[f.name] = Number(v); return; }
      if (f.dataset.optional !== undefined && v === '') return;
      if (f.tagName === 'SELECT' && f.dataset.numeric !== undefined) { if (v !== '') out[f.name] = Number(v); return; }
      out[f.name] = f.tagName === 'SELECT' ? v : v.trim();
    });
    return out;
  }

  function handleSubmit(form, work) {
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      clearErrors(form);
      var btn = form.querySelector('[type="submit"]');
      if (btn) btn.disabled = true;
      try { await work(readForm(form)); }
      catch (err) { if (err instanceof ApiError) showError(form, err); else { console.error(err); toast('Unexpected error', 'danger'); } }
      finally { if (btn) btn.disabled = false; }
    });
  }

  function fillSelect(select, items, valueKey, labelFn, placeholder) {
    var keep = select.value;
    clear(select);
    if (placeholder !== undefined) select.appendChild(el('option', { value: '' }, placeholder));
    items.forEach(function (it) { select.appendChild(el('option', { value: it[valueKey] }, labelFn(it))); });
    if (keep && Array.prototype.some.call(select.options, function (o) { return o.value === keep; })) select.value = keep;
  }

  // ---- data loading -------------------------------------------------------------------------------------------
  async function fetchAll(url, params) {
    var items = [], page = 1, total = 0;
    do {
      var data = await api('GET', url + qs(Object.assign({}, params, { per_page: 100, page: page })));
      items = items.concat(data.items); total = data.total; page++;
    } while (items.length < total);
    return items;
  }

  var vehicleCache = null;
  async function loadVehicles(force) {
    if (!vehicleCache || force) vehicleCache = await fetchAll('/api/vehicles');
    return vehicleCache;
  }

  // ---- tables ---------------------------------------------------------------------------------------------------
  // columns: [{label, render(row) -> text|Node, num: true, class}]
  function renderTable(container, columns, rows, emptyText) {
    clear(container);
    if (!rows.length) { container.appendChild(el('p', { class: 'text-body-secondary my-3' }, emptyText || 'Nothing to show.')); return; }
    container.appendChild(el('div', { class: 'table-responsive' },
      el('table', { class: 'table table-hover table-sm align-middle' },
        el('thead', null, el('tr', null, columns.map(function (c) { return el('th', { scope: 'col', class: c.num ? 'num' : '' }, c.label); }))),
        el('tbody', null, rows.map(function (r) {
          return el('tr', null, columns.map(function (c) { return el('td', { class: (c.num ? 'num ' : '') + (c.class || '') }, c.render(r)); }));
        })))));
  }

  function renderPager(container, data, onPage) {
    clear(container);
    if (!data.total) return;
    var first = (data.page - 1) * data.per_page + 1, last = Math.min(data.page * data.per_page, data.total);
    var pages = Math.ceil(data.total / data.per_page);
    container.appendChild(el('div', { class: 'pager d-flex flex-wrap justify-content-between align-items-center gap-2' },
      el('span', { class: 'text-body-secondary' }, 'Showing ' + first + '–' + last + ' of ' + data.total),
      pages > 1 ? el('div', { class: 'btn-group btn-group-sm', role: 'group', 'aria-label': 'Pages' },
        el('button', { type: 'button', class: 'btn btn-outline-secondary', disabled: data.page <= 1, onclick: function () { onPage(data.page - 1); } }, 'Previous'),
        el('span', { class: 'btn btn-outline-secondary disabled' }, 'Page ' + data.page + ' of ' + pages),
        el('button', { type: 'button', class: 'btn btn-outline-secondary', disabled: data.page >= pages, onclick: function () { onPage(data.page + 1); } }, 'Next')) : null));
  }

  // A paged, filterable table: createList({url, tableEl, pagerEl, params(), columns, empty, onLoad}) -> {reload, reset}
  function createList(cfg) {
    var page = 1, seq = 0;
    async function reload() {
      var mine = ++seq;
      try {
        var data = await api('GET', cfg.url + qs(Object.assign({}, cfg.params ? cfg.params() : {}, { page: page })));
        if (mine !== seq) return;   // a newer request superseded this one
        renderTable(cfg.tableEl, cfg.columns, data.items, cfg.empty);
        renderPager(cfg.pagerEl, data, function (p) { page = p; reload(); });
        if (cfg.onLoad) cfg.onLoad(data);
      } catch (err) { if (mine === seq) banner(cfg.tableEl, err.message, 'danger'); }
    }
    return { reload: reload, reset: function () { page = 1; return reload(); } };
  }

  function debounce(fn, ms) { var t; return function () { var a = arguments; clearTimeout(t); t = setTimeout(function () { fn.apply(null, a); }, ms); }; }

  function modal(id) { return bootstrap.Modal.getOrCreateInstance(document.getElementById(id)); }

  function today() { var d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }

  async function logout() {
    try { await api('POST', '/api/auth/logout'); } finally { location.href = '/login'; }
  }
  var logoutBtn = document.getElementById('logout');
  if (logoutBtn) logoutBtn.addEventListener('click', logout);

  window.App = { api: api, qs: qs, el: el, clear: clear, fmt: fmt, badge: badge, toast: toast, banner: banner, ApiError: ApiError,
    role: role, is: function () { return Array.prototype.indexOf.call(arguments, role) !== -1; },
    clearErrors: clearErrors, showError: showError, readForm: readForm, handleSubmit: handleSubmit, fillSelect: fillSelect,
    fetchAll: fetchAll, loadVehicles: loadVehicles, renderTable: renderTable, renderPager: renderPager, createList: createList,
    debounce: debounce, modal: modal, today: today };
})();
