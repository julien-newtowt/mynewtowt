/*
 * Sidebar behaviour — three concerns:
 *
 * 1. Collapsible nav groups (.nav-group / .nav-group-toggle) with
 *    per-group open state persisted in localStorage so each user keeps
 *    their preferred layout across reloads.
 *
 * 2. Three-mode sidebar collapse:
 *      desktop (≥1025px) — sidebar wide; `.collapsed` shrinks it to 64px.
 *      tablet  (769-1024px) — sidebar collapsed by default; `.expanded`
 *        shows the wide variant temporarily.
 *      phone   (≤768px) — sidebar hidden; the `.expanded` class brings it
 *        in as an overlay.
 *    Persisted per-mode (towt_sidebar_desktop, towt_sidebar_tablet).
 *
 * 3. Active link highlight by exact path match. Opens the parent group
 *    of the active link so the active item stays visible after reload.
 */
(function () {
  "use strict";

  var GROUPS_KEY = "newtowt.sidebar.groups";
  var DESKTOP_KEY = "towt_sidebar_desktop";
  var TABLET_KEY = "towt_sidebar_tablet";

  function readJson(key) {
    try { return JSON.parse(localStorage.getItem(key) || "{}"); }
    catch (e) { return {}; }
  }
  function writeJson(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch (e) { /* quota / private mode */ }
  }

  function mode() {
    if (window.matchMedia("(max-width: 768px)").matches) return "phone";
    if (window.matchMedia("(max-width: 1024px)").matches) return "tablet";
    return "desktop";
  }

  function applySidebarMode() {
    var sb = document.querySelector(".sidebar");
    if (!sb) return;
    sb.classList.remove("expanded", "collapsed");
    var m = mode();
    if (m === "desktop") {
      if (localStorage.getItem(DESKTOP_KEY) === "collapsed") sb.classList.add("collapsed");
    } else if (m === "tablet") {
      if (localStorage.getItem(TABLET_KEY) === "expanded") sb.classList.add("expanded");
    }
    // phone: always start hidden; user opens via hamburger
  }

  function toggleSidebar() {
    var sb = document.querySelector(".sidebar");
    if (!sb) return;
    var m = mode();
    if (m === "desktop") {
      sb.classList.toggle("collapsed");
      localStorage.setItem(DESKTOP_KEY, sb.classList.contains("collapsed") ? "collapsed" : "expanded");
    } else {
      // tablet + phone: use .expanded as overlay/show
      sb.classList.toggle("expanded");
      if (m === "tablet") {
        localStorage.setItem(TABLET_KEY, sb.classList.contains("expanded") ? "expanded" : "collapsed");
      }
    }
  }

  function bindGroups() {
    var state = readJson(GROUPS_KEY);
    document.querySelectorAll(".nav-group").forEach(function (group) {
      var key = group.dataset.group
        || (group.querySelector(".nav-group-title")
            && group.querySelector(".nav-group-title").textContent.trim());
      if (!key) return;

      var isOpen = state[key] === undefined ? true : !!state[key];
      group.classList.toggle("open", isOpen);

      var toggle = group.querySelector(".nav-group-toggle");
      if (!toggle || toggle.dataset.bound === "1") return;
      toggle.dataset.bound = "1";
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");

      toggle.addEventListener("click", function (e) {
        e.preventDefault();
        var nowOpen = !group.classList.contains("open");
        group.classList.toggle("open", nowOpen);
        toggle.setAttribute("aria-expanded", nowOpen ? "true" : "false");
        state[key] = nowOpen;
        writeJson(GROUPS_KEY, state);
      });
    });
  }

  function highlightActive() {
    var here = window.location.pathname;
    var links = Array.prototype.slice.call(
      document.querySelectorAll(".sidebar nav a[href]")
    );

    // Un lien "matche" si exact OU si le chemin courant commence par
    // href + "/" (frontière de path — évite que /me matche /medical).
    function matches(href) {
      if (!href || href === "#") return false;
      if (href === here) return true;
      if (href === "/") return false; // accueil : match exact seulement
      return here === href || here.indexOf(href + "/") === 0;
    }

    // On ne garde QUE le match le plus spécifique (href le plus long)
    // pour qu'un seul bouton soit en surbrillance, même quand /cargo et
    // /cargo/booking sont tous deux des entrées de menu.
    var best = null;
    var bestLen = -1;
    links.forEach(function (a) {
      a.removeAttribute("aria-current");
      var href = a.getAttribute("href");
      if (matches(href) && href.length > bestLen) {
        best = a;
        bestLen = href.length;
      }
    });

    if (best) {
      best.setAttribute("aria-current", "page");
      var group = best.closest(".nav-group");
      if (group && !group.classList.contains("open")) {
        group.classList.add("open");
        var t = group.querySelector(".nav-group-toggle");
        if (t) t.setAttribute("aria-expanded", "true");
      }
    }
  }

  function bindHamburger() {
    document.querySelectorAll("[data-sidebar-toggle]").forEach(function (btn) {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        toggleSidebar();
      });
    });
  }

  function init() {
    applySidebarMode();
    bindGroups();
    highlightActive();
    bindHamburger();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
  window.addEventListener("resize", applySidebarMode);

  window.toggleSidebar = toggleSidebar;
})();
