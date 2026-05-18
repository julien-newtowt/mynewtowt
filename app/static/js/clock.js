/*
 * International clock — local time + Paris time.
 * Renders into any element with class .clock-widget.
 * Update interval: 1s (only updates DOM text, no layout reflow).
 */
(function () {
  "use strict";

  function fmt(date, tz) {
    var opts = {
      timeZone: tz,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    };
    try {
      return new Intl.DateTimeFormat("fr-FR", opts).format(date);
    } catch (e) {
      return "--:--";
    }
  }

  function tzLabel(tz) {
    // Short, readable label
    if (tz === "Europe/Paris") return "Paris";
    try {
      return new Intl.DateTimeFormat("fr-FR", {
        timeZone: tz,
        timeZoneName: "short"
      })
        .formatToParts(new Date())
        .filter(function (p) { return p.type === "timeZoneName"; })
        .map(function (p) { return p.value; })
        .join("") || tz;
    } catch (e) {
      return tz;
    }
  }

  function tick() {
    var now = new Date();
    var local = fmt(now, Intl.DateTimeFormat().resolvedOptions().timeZone);
    var paris = fmt(now, "Europe/Paris");

    document.querySelectorAll(".clock-widget").forEach(function (el) {
      var localEl = el.querySelector("[data-clock=local]");
      var parisEl = el.querySelector("[data-clock=paris]");
      if (localEl) localEl.textContent = local;
      if (parisEl) parisEl.textContent = paris;
    });
  }

  function init() {
    tick();
    setInterval(tick, 1000);

    // Inject readable labels once (browser-side detection of user tz)
    var userTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    document.querySelectorAll(".clock-widget [data-tz=local-label]").forEach(function (el) {
      el.textContent = tzLabel(userTz);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
