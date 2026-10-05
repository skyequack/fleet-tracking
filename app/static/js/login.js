(function () {
  'use strict';
  var form = document.getElementById('login-form');
  // Only follow a ?next= that stays on this site: one leading slash, not "//host" or "/\host".
  var SAME_SITE = /^\/(?![\/\\])/;
  function target() {
    var next = new URLSearchParams(location.search).get('next');
    return next && SAME_SITE.test(next) ? next : '/';
  }
  App.handleSubmit(form, async function (values) {
    await App.api('POST', '/api/auth/login', values);
    location.href = target();   // "/" sends each role to its own start page
  });
})();
