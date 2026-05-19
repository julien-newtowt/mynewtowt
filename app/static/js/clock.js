/*
 * International clocks — UTC, Paris, and the next destination ports of
 * the fleet (fetched from /api/v1/ports/next-clocks).
 *
 * Two rendering targets are supported:
 *   1. Sidebar widget — <div id="sidebar-clock"> populated by this script
 *      with rows UTC / Paris / Port-A / Port-B. The first non-UTC port
 *      timezone is stored on data-port-tz for the TOWT_TZ helpers.
 *   2. Topbar widget — <div class="clock-widget"> with [data-clock=local]
 *      and [data-clock=paris] spans. Used by the topbar.
 *
 * Update cadence: tick every 30 s (no seconds shown — keeps things
 * readable on mobile and avoids constant DOM mutation).
 */
(function () {
  "use strict";

  var portClocks = []; // { tz, label, elId }

  function fmt(tz) {
    try {
      return new Intl.DateTimeFormat("fr-FR", {
        timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false,
      }).format(new Date());
    } catch (e) { return "--:--"; }
  }

  function setText(el, text) { if (el) el.textContent = text; }

  function tick() {
    // Topbar widget
    var userTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    document.querySelectorAll(".clock-widget").forEach(function (el) {
      setText(el.querySelector("[data-clock=local]"), fmt(userTz));
      setText(el.querySelector("[data-clock=paris]"), fmt("Europe/Paris"));
    });

    // Sidebar widget
    setText(document.getElementById("clock-utc"), fmt("UTC"));
    setText(document.getElementById("clock-paris"), fmt("Europe/Paris"));
    portClocks.forEach(function (pc) {
      setText(document.getElementById(pc.elId), fmt(pc.tz));
    });
  }

  function tzLabel(tz) {
    if (tz === "Europe/Paris") return "Paris";
    try {
      return new Intl.DateTimeFormat("fr-FR", {
        timeZone: tz, timeZoneName: "short",
      })
        .formatToParts(new Date())
        .filter(function (p) { return p.type === "timeZoneName"; })
        .map(function (p) { return p.value; }).join("") || tz;
    } catch (e) { return tz; }
  }

  function injectLabels() {
    var userTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    document.querySelectorAll(".clock-widget [data-tz=local-label]").forEach(function (el) {
      el.textContent = tzLabel(userTz);
    });
  }

  function loadPortClocks() {
    var sidebar = document.getElementById("sidebar-clock");
    if (!sidebar) return;
    var container = document.getElementById("clock-port-rows");
    if (!container) return;
    fetch("/api/v1/ports/next-clocks", {
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
    })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (clocks) {
        if (!Array.isArray(clocks) || !clocks.length) return;
        container.innerHTML = "<hr class=\"clock-port-separator\">";
        clocks.slice(0, 3).forEach(function (c, i) {
          if (!c || !c.timezone) return;
          var elId = "clock-port-" + i;
          var row = document.createElement("div");
          row.className = "clock-row";
          row.innerHTML =
            "<span class=\"clock-label\" title=\"" +
            (c.locode || "") + "\">" + (c.label || c.port_name || c.locode || "Port") +
            "</span><span class=\"clock-time\" id=\"" + elId + "\">--:--</span>";
          container.appendChild(row);
          portClocks.push({ tz: c.timezone, label: c.port_name || c.label, elId: elId });
        });
        if (portClocks.length > 0) sidebar.dataset.portTz = portClocks[0].tz;
        tick();
      })
      .catch(function () { /* offline tolerant */ });
  }

  function init() {
    injectLabels();
    tick();
    setInterval(tick, 30000);
    loadPortClocks();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
