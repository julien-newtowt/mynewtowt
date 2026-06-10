/*
 * Topbar dropdowns — exclusive open (one menu at a time), backdrop click
 * and Escape close them. Works for both staff and client layouts.
 *
 * Markup: <button class="topbar-action" data-menu-toggle="topbar-user-menu">
 *         <div class="topbar-menu" id="topbar-user-menu">...</div>
 */
(function () {
  "use strict";

  function closeAll(except) {
    document.querySelectorAll(".topbar-menu.open").forEach(function (m) {
      if (m !== except) m.classList.remove("open");
    });
  }

  function bind() {
    document.querySelectorAll("[data-menu-toggle]").forEach(function (btn) {
      if (btn.dataset.menuBound === "1") return;
      btn.dataset.menuBound = "1";
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var menu = document.getElementById(btn.dataset.menuToggle);
        if (!menu) return;
        var wasOpen = menu.classList.contains("open");
        closeAll(menu);
        menu.classList.toggle("open", !wasOpen);
      });
    });

    // Legacy IDs still supported via id-based hookup
    var legacyPairs = [
      ["topbar-notif-btn", "topbar-notif-menu"],
      ["topbar-user-btn", "topbar-user-menu"],
    ];
    legacyPairs.forEach(function (pair) {
      var btn = document.getElementById(pair[0]);
      var menu = document.getElementById(pair[1]);
      if (!btn || !menu || btn.dataset.menuBound === "1") return;
      btn.dataset.menuBound = "1";
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var wasOpen = menu.classList.contains("open");
        closeAll(menu);
        menu.classList.toggle("open", !wasOpen);
      });
    });
  }

  document.addEventListener("click", function (e) {
    if (e.target.closest(".topbar-action") || e.target.closest(".topbar-menu")) return;
    closeAll(null);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll(null);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
  document.body && document.body.addEventListener("htmx:afterSwap", bind);
})();
