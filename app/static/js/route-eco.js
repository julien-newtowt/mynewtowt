/*
 * route-eco.js — Per-lot CO₂ estimator for the route detail eco-calculator.
 *
 * Progressive enhancement: the server renders the per-tonne values (tonnage = 1)
 * so the block is meaningful without JS. When JS is available, changing the
 * tonnage input rescales the avoided-CO₂ figure and the three equivalences
 * (trees / Paris–NY flights / truck km) live.
 *
 * Expected HTML (see public/route_detail.html):
 *
 *   <div data-eco-lot
 *        data-avoided-per-t="61.2"   ← kg CO₂ avoided per tonne
 *        data-tree-kg="25"           ← kg CO₂ absorbed per tree per year
 *        data-flight-kg="1000"       ← kg CO₂ per Paris–NY flight (1 pax)
 *        data-truck-kg="0.9"         ← kg CO₂ per truck km
 *        data-lang="fr">
 *     <input data-eco-tonnage value="1">
 *     <strong data-eco-lot-value>61.2</strong>
 *   </div>
 *   …
 *   <span data-eco-eq-trees>…</span>
 *   <span data-eco-eq-flights>…</span>
 *   <span data-eco-eq-truck>…</span>
 *
 * CSP-strict compatible (no inline scripts). Loaded via <script src defer>.
 */
(function () {
  "use strict";

  function initEcoLot() {
    var box = document.querySelector("[data-eco-lot]");
    if (!box) return;

    var input = box.querySelector("[data-eco-tonnage]");
    var lotValue = box.querySelector("[data-eco-lot-value]");
    if (!input || !lotValue) return;

    var avoidedPerT = parseFloat(box.dataset.avoidedPerT) || 0;
    var treeKg = parseFloat(box.dataset.treeKg) || 0;
    var flightKg = parseFloat(box.dataset.flightKg) || 0;
    var truckKg = parseFloat(box.dataset.truckKg) || 0;
    var locale = (box.dataset.lang || "fr").replace("_", "-");

    var treesEl = document.querySelector("[data-eco-eq-trees]");
    var flightsEl = document.querySelector("[data-eco-eq-flights]");
    var truckEl = document.querySelector("[data-eco-eq-truck]");

    function fmt(value, decimals) {
      try {
        return new Intl.NumberFormat(locale, {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        }).format(value);
      } catch (e) {
        return value.toFixed(decimals);
      }
    }

    function recompute() {
      var tonnage = parseFloat(input.value);
      if (!isFinite(tonnage) || tonnage < 0) tonnage = 0;

      var lotAvoided = avoidedPerT * tonnage;

      lotValue.textContent = fmt(lotAvoided, 1);
      if (treesEl && treeKg) treesEl.textContent = fmt(lotAvoided / treeKg, 1);
      if (flightsEl && flightKg) flightsEl.textContent = fmt(lotAvoided / flightKg, 2);
      if (truckEl && truckKg) truckEl.textContent = fmt(lotAvoided / truckKg, 0);
    }

    input.addEventListener("input", recompute);
    recompute();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initEcoLot);
  } else {
    initEcoLot();
  }
})();
