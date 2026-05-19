/*
 * HTMX CSRF — auto-inject the towt_csrf cookie value as x-csrf-token
 * header on every HTMX request. The cookie is set by CSRFMiddleware
 * on first response.
 */
(function () {
  "use strict";

  function getCsrf() {
    var match = document.cookie.split("; ").find(function (r) {
      return r.indexOf("towt_csrf=") === 0;
    });
    return match ? match.split("=")[1] : null;
  }

  function bind() {
    if (!document.body) return;
    document.body.addEventListener("htmx:configRequest", function (evt) {
      var token = getCsrf();
      if (token) evt.detail.headers["x-csrf-token"] = token;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
