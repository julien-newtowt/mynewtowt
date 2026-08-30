/*
 * Toast helper — small non-blocking notifications.
 *
 * Usage:
 *   showToast("Réservation confirmée");
 *   showToast("Erreur : capacité dépassée", "error");
 *   showToast("Attention : conflit port", "warn");
 *
 * Types: success (default), error, warn, info.
 * Toasts auto-dismiss after 4 s; click ✕ to close.
 */
(function () {
  "use strict";

  function ensureContainer() {
    var c = document.getElementById("toast-container");
    if (!c) {
      c = document.createElement("div");
      c.id = "toast-container";
      c.className = "toast-container";
      document.body.appendChild(c);
    }
    return c;
  }

  function show(message, type) {
    type = type || "success";
    var c = ensureContainer();
    var t = document.createElement("div");
    t.className = "toast toast-" + type;

    var msg = document.createElement("span");
    msg.textContent = message;

    var btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("aria-label", "Fermer");
    btn.textContent = "×";
    btn.addEventListener("click", function () { remove(t); });

    t.appendChild(msg);
    t.appendChild(btn);
    c.appendChild(t);

    // Force reflow then add visible class for the fade-in transition
    /* eslint-disable no-unused-expressions */
    t.offsetWidth;
    t.classList.add("toast-visible");

    setTimeout(function () { remove(t); }, 4000);
  }

  function remove(t) {
    if (!t || !t.parentNode) return;
    t.classList.remove("toast-visible");
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 250);
  }

  window.showToast = show;

  // Auto-pickup of HX-Trigger toast events: server can respond with
  // HX-Trigger: {"toast":{"message":"...","type":"success"}}
  document.addEventListener("htmx:afterRequest", function (evt) {
    var trigger = evt.detail.xhr && evt.detail.xhr.getResponseHeader("HX-Trigger");
    if (!trigger) return;
    try {
      var parsed = JSON.parse(trigger);
      if (parsed.toast) show(parsed.toast.message, parsed.toast.type);
    } catch (e) { /* not JSON, ignore */ }
  });

  // Mutations shipped as hx-post + hx-swap="none" never swap on a non-2xx
  // response (htmx only swaps on success), so a 409 (verrou/gel) or 400
  // (métier) response was silently dropped with no htmx:afterRequest toast
  // either. Surface it here: FastAPI's HTTPException body is
  // {"detail": "..."} — toast that verbatim; otherwise fall back to a
  // generic bilingual message. Same path for 4xx and 5xx: we don't try to
  // distinguish, there is nothing more useful to say without a body.
  document.addEventListener("htmx:responseError", function (evt) {
    var xhr = evt.detail && evt.detail.xhr;
    var message = null;
    if (xhr && xhr.responseText) {
      try {
        var body = JSON.parse(xhr.responseText);
        if (body && typeof body.detail === "string") message = body.detail;
      } catch (e) { /* not JSON, ignore */ }
    }
    if (!message) {
      message = "Action refusée — rechargez la page / Action rejected — reload the page";
    }
    show(message, "error");
  });
})();
