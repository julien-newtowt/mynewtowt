/*
 * Lucide icons — initialize on load and refresh after every HTMX swap.
 * Markup: <i data-lucide="anchor"></i> is replaced by an <svg> at runtime.
 */
(function () {
  "use strict";

  function refresh() {
    if (typeof window.lucide !== "undefined" && window.lucide.createIcons) {
      try { window.lucide.createIcons(); } catch (e) { /* ignore */ }
    }
  }

  function start() {
    refresh();
    document.body.addEventListener("htmx:afterSwap", refresh);
    document.body.addEventListener("htmx:load", refresh);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
