/*
 * CSRF for plain forms — auto-inject the ``towt_csrf`` cookie value as a
 * hidden ``_csrf`` field on every non-HTMX POST form.
 *
 * Rationale: csrf-htmx.js only injects the x-csrf-token header on HTMX
 * requests. Plain ``<form method="post">`` submissions otherwise carry no
 * token and are rejected by CSRFMiddleware ("CSRF validation failed").
 * This script guarantees every same-origin POST form gets the token —
 * complementing the server-side hidden field where present.
 *
 * CSP-safe: loaded as an external file (no inline script).
 */
(function () {
  "use strict";

  function getCsrf() {
    var match = document.cookie.split("; ").find(function (r) {
      return r.indexOf("towt_csrf=") === 0;
    });
    return match ? match.split("=")[1] : null;
  }

  function isHtmxForm(form) {
    return (
      form.hasAttribute("hx-post") ||
      form.hasAttribute("hx-put") ||
      form.hasAttribute("hx-patch") ||
      form.hasAttribute("hx-delete")
    );
  }

  function inject(form) {
    var method = (form.getAttribute("method") || "get").toLowerCase();
    if (method !== "post") return;
    if (isHtmxForm(form)) return; // header injected by csrf-htmx.js
    if (form.querySelector('input[name="_csrf"]')) return; // already present
    var token = getCsrf();
    if (!token) return;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "_csrf";
    input.value = token;
    form.appendChild(input);
  }

  function injectAll() {
    var forms = document.querySelectorAll('form[method="post"], form[method="POST"]');
    Array.prototype.forEach.call(forms, inject);
  }

  function bind() {
    injectAll();
    // Filet de sécurité : (ré)injecte juste avant l'envoi (formulaires ajoutés
    // dynamiquement, token rafraîchi…). Capture pour passer avant la soumission.
    document.addEventListener(
      "submit",
      function (e) {
        if (e.target && e.target.tagName === "FORM") inject(e.target);
      },
      true
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
