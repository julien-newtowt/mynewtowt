/*
 * route-tabs.js — Tab switcher for the route detail page.
 *
 * Expected HTML structure:
 *
 *   <div class="tabs">
 *     <button class="js-tab-btn is-active" data-target="panel-overview">Overview</button>
 *     <button class="js-tab-btn"           data-target="panel-schedule">Schedule</button>
 *     <button class="js-tab-btn"           data-target="panel-cargo">Cargo</button>
 *   </div>
 *
 *   <section id="panel-overview" class="js-tab-pane">…</section>
 *   <section id="panel-schedule" class="js-tab-pane" hidden>…</section>
 *   <section id="panel-cargo"    class="js-tab-pane" hidden>…</section>
 *
 * On click of a .js-tab-btn:
 *   1. Remove is-active from all .js-tab-btn elements.
 *   2. Add is-active to the clicked button.
 *   3. Set the hidden attribute on all .js-tab-pane elements.
 *   4. Remove hidden from the panel whose id matches data-target.
 *
 * Compatible with CSP strict (no inline scripts).
 * Loaded via <script src="…" defer> in the page.
 */
(function () {
  "use strict";

  function initTabs() {
    var buttons = document.querySelectorAll(".js-tab-btn");
    if (!buttons.length) return;

    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var targetId = btn.dataset.target;
        if (!targetId) return;

        /* 1 & 2 — update active state on all tab buttons. */
        buttons.forEach(function (b) { b.classList.remove("is-active"); });
        btn.classList.add("is-active");

        /* 3 & 4 — show the target panel, hide the rest. */
        document.querySelectorAll(".js-tab-pane").forEach(function (pane) {
          if (pane.id === targetId) {
            pane.removeAttribute("hidden");
          } else {
            pane.setAttribute("hidden", "");
          }
        });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTabs);
  } else {
    initTabs();
  }
})();
