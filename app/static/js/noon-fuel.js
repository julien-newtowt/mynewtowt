// Noon report — auto-calcul conso totale (somme moteurs) + ROB (précédent − conso).
// Les deux champs restent éditables ; toute saisie manuelle désactive l'auto
// pour ce champ et affiche une alerte.
(function () {
  "use strict";

  function num(el) {
    var v = parseFloat(el && el.value);
    return isNaN(v) ? 0 : v;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var total = document.getElementById("noon-total-conso");
    var rob = document.getElementById("noon-rob-do");
    if (!total || !rob) return;

    var engines = Array.prototype.slice.call(document.querySelectorAll(".eng-do"));
    var alertBox = document.getElementById("noon-fuel-alert");
    var prevRob = parseFloat(rob.getAttribute("data-prev-rob"));
    var hasPrev = !isNaN(prevRob);

    function showAlert() {
      if (alertBox) alertBox.removeAttribute("hidden");
    }

    function recompute() {
      var sum = engines.reduce(function (a, el) {
        return a + num(el);
      }, 0);
      if (total.dataset.overridden !== "1") {
        total.value = sum ? sum.toFixed(4) : "";
      }
      if (rob.dataset.overridden !== "1" && hasPrev) {
        var t = parseFloat(total.value);
        if (isNaN(t)) t = 0;
        rob.value = (prevRob - t).toFixed(2);
      }
    }

    engines.forEach(function (el) {
      el.addEventListener("input", recompute);
    });
    total.addEventListener("input", function () {
      total.dataset.overridden = "1";
      showAlert();
      recompute(); // ROB suit le total saisi (sauf si ROB aussi forcé)
    });
    rob.addEventListener("input", function () {
      rob.dataset.overridden = "1";
      showAlert();
    });

    recompute();
  });
})();
