/* Composant « équivalences CO₂ » — réutilisable (fiche route, /impact,
   certificat, kit B2B2C). Externe → compatible CSP stricte (pas d'inline).

   Initialise chaque bloc [data-co2eq] : un curseur + une saisie numérique
   pilotent trois équivalences (arbres/an, vols Paris–NY, km camion).
   Hypothèses portées en data-* pour rester ajustables sans toucher au JS. */
(function () {
  "use strict";

  function num(el, attr, fallback) {
    var v = parseFloat(el.getAttribute(attr));
    return isNaN(v) ? fallback : v;
  }

  function initWidget(root) {
    var slider = root.querySelector("[data-co2eq-slider]");
    var input = root.querySelector("[data-co2eq-num]");
    if (!slider) return;

    var elVal = root.querySelector("[data-co2eq-display]");
    var elTrees = root.querySelector("[data-co2eq-trees]");
    var elFlights = root.querySelector("[data-co2eq-flights]");
    var elKm = root.querySelector("[data-co2eq-km]");

    var locale = root.getAttribute("data-co2eq-locale") || "fr-FR";
    var max = num(slider, "max", 2000);
    var step = num(slider, "step", 10) || 1;
    // Hypothèses (kg CO₂) — surchargées via data-* sur le conteneur.
    var fTree = num(root, "data-co2eq-tree", 25);
    var fFlight = num(root, "data-co2eq-flight", 1000);
    var fTruck = num(root, "data-co2eq-truck", 0.9);

    var nf0 = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
    var nf1 = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });

    function render(kg) {
      kg = Math.max(0, Math.min(max, Math.round(kg / step) * step));
      if (elVal) elVal.textContent = nf0.format(kg);
      if (elTrees) {
        var trees = kg / fTree;
        elTrees.textContent = trees >= 10 ? nf0.format(Math.round(trees)) : nf1.format(trees);
      }
      if (elFlights) elFlights.textContent = nf1.format(kg / fFlight);
      if (elKm) elKm.textContent = nf0.format(Math.round(kg / fTruck));
      slider.style.setProperty("--co2eq-fill", (kg / max) * 100 + "%");
    }

    slider.addEventListener("input", function (e) {
      if (input) input.value = e.target.value;
      render(parseFloat(e.target.value));
    });
    if (input) {
      input.addEventListener("input", function (e) {
        var v = Math.max(0, Math.min(max, parseFloat(e.target.value) || 0));
        slider.value = v;
        render(v);
      });
    }

    render(parseFloat(slider.value) || 0);
  }

  function initAll() {
    var nodes = document.querySelectorAll("[data-co2eq]");
    for (var i = 0; i < nodes.length; i++) initWidget(nodes[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
