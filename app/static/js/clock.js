/*
 * Topbar clocks — local user time + Paris reference.
 *
 * Cible : <div class="clock-widget"> avec [data-clock=local] + [data-clock=paris]
 * et [data-tz=local-label] pour le libellé du fuseau utilisateur.
 *
 * Tick toutes les 30 s (pas de secondes affichées — évite les reflows).
 */
(function () {
  "use strict";

  function fmt(tz) {
    try {
      return new Intl.DateTimeFormat("fr-FR", {
        timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false,
      }).format(new Date());
    } catch (e) { return "--:--"; }
  }

  function setText(el, text) { if (el) el.textContent = text; }

  function tick() {
    var userTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    document.querySelectorAll(".clock-widget").forEach(function (el) {
      setText(el.querySelector("[data-clock=local]"), fmt(userTz));
      setText(el.querySelector("[data-clock=paris]"), fmt("Europe/Paris"));
    });
  }

  function tzShort(tz) {
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
      el.textContent = tzShort(userTz);
    });
  }

  function init() {
    injectLabels();
    tick();
    setInterval(tick, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
