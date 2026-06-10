/*
 * Modal helper — HTMX-friendly overlay container.
 *
 * Usage in templates:
 *   <button onclick="loadModal('/cargo/123/edit')">Modifier</button>
 *
 * The fetched HTML is injected into #modal-container which receives the
 * .modal-open class. Pressing Escape or clicking the backdrop closes it.
 *
 * For HTMX-triggered modal opens, server can respond with:
 *   HX-Trigger: {"modal":{"url":"/foo/bar"}}
 */
(function () {
  "use strict";

  function ensureContainer() {
    var c = document.getElementById("modal-container");
    if (!c) {
      c = document.createElement("div");
      c.id = "modal-container";
      c.className = "modal-container";
      document.body.appendChild(c);
    }
    return c;
  }

  function open(html) {
    var c = ensureContainer();
    c.innerHTML = html;
    c.classList.add("modal-open");
    document.body.classList.add("modal-noscroll");
    // Refresh lucide icons inside the modal content
    if (typeof window.lucide !== "undefined" && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (e) { /* ignore */ }
    }
  }

  function close() {
    var c = document.getElementById("modal-container");
    if (c) {
      c.classList.remove("modal-open");
      c.innerHTML = "";
    }
    document.body.classList.remove("modal-noscroll");
  }

  function loadModal(url) {
    fetch(url, {
      credentials: "same-origin",
      headers: { "Accept": "text/html", "HX-Request": "true" }
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(open)
      .catch(function (err) {
        // Lit la lang depuis <html lang="..."> (positionné par le serveur).
        var msg = (document.documentElement.lang === 'fr')
          ? "Erreur de chargement : " + err.message
          : "Loading error: " + err.message;
        if (window.showToast) window.showToast(msg, "error");
        else console.error(err);
      });
  }

  window.loadModal = loadModal;
  window.closeModal = close;

  // Backdrop click → close (only when clicking the container itself,
  // not its inner .modal-card).
  document.addEventListener("click", function (e) {
    var c = document.getElementById("modal-container");
    if (!c || !c.classList.contains("modal-open")) return;
    if (e.target === c) close();
  });

  // Escape key → close
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });

  // Auto-pickup of HX-Trigger modal events
  document.addEventListener("htmx:afterRequest", function (evt) {
    var trigger = evt.detail.xhr && evt.detail.xhr.getResponseHeader("HX-Trigger");
    if (!trigger) return;
    try {
      var parsed = JSON.parse(trigger);
      if (parsed.modal && parsed.modal.url) loadModal(parsed.modal.url);
      if (parsed["modal-close"]) close();
    } catch (e) { /* not JSON, ignore */ }
  });
})();
