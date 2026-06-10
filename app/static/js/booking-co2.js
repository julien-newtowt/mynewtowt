/*
 * Booking — Estimateur CO₂ live (étape 2).
 *
 * Recalcule à chaque modification des champs items-N-count et items-N-unit_weight_kg
 * la consommation CO₂ NEWTOWT et l'évitement vs cargo conventionnel.
 *
 * Markup attendu :
 *   <div id="co2-estimator"
 *        data-distance-nm="3200.5"
 *        data-towt-ef-g-tkm="1.5"
 *        data-conv-ef-g-tkm="13.7"
 *        data-default-weight-kg="500">
 *     <span class="co2-distance"></span>
 *     <span class="co2-tonnage"></span>
 *     <span class="co2-towt"></span>
 *     <span class="co2-conv"></span>
 *     <span class="co2-avoided"></span>
 *     <span class="co2-pct"></span>
 *   </div>
 *
 * Defaults marketing : 500 kg / palette si poids unitaire non renseigné.
 */
(function () {
  "use strict";

  var NM_TO_KM = 1.852;

  function readNumber(el, attr, fallback) {
    var v = parseFloat(el.getAttribute(attr));
    return isFinite(v) ? v : fallback;
  }

  function fmt(value, digits) {
    if (digits === undefined) digits = 1;
    if (!isFinite(value)) return "—";
    return value.toLocaleString("fr-FR", {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    });
  }

  function bind() {
    var el = document.getElementById("co2-estimator");
    if (!el) return;

    var distance_nm = readNumber(el, "data-distance-nm", 0);
    var towt_ef = readNumber(el, "data-towt-ef-g-tkm", 1.5);
    var conv_ef = readNumber(el, "data-conv-ef-g-tkm", 13.7);
    var default_weight = readNumber(el, "data-default-weight-kg", 500);
    var distance_km = distance_nm * NM_TO_KM;

    function totalTonnage() {
      var tonnage_kg = 0;
      // Parcours toutes les paires count + unit_weight_kg
      document.querySelectorAll('[name$="-count"]').forEach(function (cnt) {
        var n = parseInt(cnt.value || "0", 10);
        if (!isFinite(n) || n <= 0) return;
        var name = cnt.getAttribute("name");
        var prefix = name.replace(/-count$/, "");
        var w_el = document.querySelector('[name="' + prefix + '-unit_weight_kg"]');
        var w = w_el ? parseFloat(w_el.value || "0") : 0;
        if (!isFinite(w) || w <= 0) w = default_weight;
        tonnage_kg += n * w;
      });
      return tonnage_kg / 1000.0; // kg → t
    }

    function recompute() {
      var tonnage_t = totalTonnage();
      var tkm = distance_km * tonnage_t;
      var towt_kg = tkm * towt_ef / 1000.0;
      var conv_kg = tkm * conv_ef / 1000.0;
      var avoided_kg = conv_kg - towt_kg;
      var pct = conv_kg > 0 ? (100 * avoided_kg / conv_kg) : 0;

      _set(el, ".co2-distance", fmt(distance_km, 0) + " km");
      _set(el, ".co2-tonnage", fmt(tonnage_t, 2) + " t");
      _set(el, ".co2-towt", fmt(towt_kg, 1) + " kg");
      _set(el, ".co2-conv", fmt(conv_kg, 1) + " kg");
      _set(el, ".co2-avoided", fmt(avoided_kg, 1) + " kg");
      _set(el, ".co2-pct", fmt(pct, 1) + " %");

      // Visibility : montre le bloc dès qu'on a une tonnage > 0
      el.classList.toggle("co2-active", tonnage_t > 0);
    }

    function _set(root, selector, value) {
      root.querySelectorAll(selector).forEach(function (n) { n.textContent = value; });
    }

    document.addEventListener("input", function (e) {
      if (!e.target || !e.target.name) return;
      if (/-count$|-unit_weight_kg$/.test(e.target.name)) {
        recompute();
      }
    });
    recompute();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
