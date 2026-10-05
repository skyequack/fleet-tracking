// Follow the operating system's light/dark setting (Bootstrap 5.3 colour modes). Loaded in <head> to avoid a flash.
(function () {
  'use strict';
  var query = window.matchMedia('(prefers-color-scheme: dark)');
  function apply() {
    document.documentElement.setAttribute('data-bs-theme', query.matches ? 'dark' : 'light');
    document.dispatchEvent(new CustomEvent('themechange'));
  }
  apply();
  if (query.addEventListener) query.addEventListener('change', apply);
})();
