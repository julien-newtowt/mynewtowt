/*
 * Sidebar — collapsible groups with localStorage persistence.
 * Each .nav-group has a <button class="nav-group-toggle"> that toggles
 * the .open class on its parent. State persists across pages.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "newtowt.sidebar.groups";

  function readState() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch (e) {
      return {};
    }
  }
  function writeState(state) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) {}
  }

  function init() {
    var state = readState();

    document.querySelectorAll(".nav-group").forEach(function (group) {
      var key = group.dataset.group || group.querySelector(".nav-group-title")?.textContent?.trim();
      if (!key) return;

      // Default: open on first render unless explicitly closed.
      var isOpen = state[key] === undefined ? true : !!state[key];
      group.classList.toggle("open", isOpen);

      var toggle = group.querySelector(".nav-group-toggle");
      if (!toggle) return;
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");

      toggle.addEventListener("click", function (e) {
        e.preventDefault();
        var nowOpen = !group.classList.contains("open");
        group.classList.toggle("open", nowOpen);
        toggle.setAttribute("aria-expanded", nowOpen ? "true" : "false");
        state[key] = nowOpen;
        writeState(state);
      });
    });

    // Highlight the active link by exact path
    var here = window.location.pathname;
    document.querySelectorAll(".sidebar nav a[href]").forEach(function (a) {
      if (a.getAttribute("href") === here) {
        a.setAttribute("aria-current", "page");
        // Open the parent group too if collapsed
        var group = a.closest(".nav-group");
        if (group && !group.classList.contains("open")) {
          group.classList.add("open");
          var t = group.querySelector(".nav-group-toggle");
          if (t) t.setAttribute("aria-expanded", "true");
        }
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
