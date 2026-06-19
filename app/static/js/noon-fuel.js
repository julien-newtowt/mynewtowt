// Noon report — auto-calculs (tous éditables ; saisie manuelle = override + alerte).
//  • Conso totale = somme des « Conso DO » moteurs (ce report).
//  • ROB DO = ROB du report précédent − conso.
//  • ETA 7,0→9,0 kt = maintenant + distance_to_go / vitesse.
(function () {
  "use strict";

  function num(el) {
    var v = parseFloat(el && el.value);
    return isNaN(v) ? 0 : v;
  }

  function initFuel() {
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
      recompute();
    });
    rob.addEventListener("input", function () {
      rob.dataset.overridden = "1";
      showAlert();
    });
    recompute();
  }

  function initEtaBySpeed() {
    var dtg = document.querySelector('[name="distance_to_go_nm"]');
    if (!dtg) return;
    var speeds = { eta_70_kt: 7.0, eta_75_kt: 7.5, eta_80_kt: 8.0, eta_85_kt: 8.5, eta_90_kt: 9.0 };
    function fmt(d) {
      var p = function (n) {
        return (n < 10 ? "0" : "") + n;
      };
      return (
        d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
        "T" + p(d.getHours()) + ":" + p(d.getMinutes())
      );
    }
    function recompute() {
      var dist = parseFloat(dtg.value);
      Object.keys(speeds).forEach(function (name) {
        var el = document.querySelector('[name="' + name + '"]');
        if (!el || el.dataset.overridden === "1") return;
        if (isNaN(dist) || dist <= 0) return;
        var eta = new Date(Date.now() + (dist / speeds[name]) * 3600 * 1000);
        el.value = fmt(eta);
      });
    }
    Object.keys(speeds).forEach(function (name) {
      var el = document.querySelector('[name="' + name + '"]');
      if (el)
        el.addEventListener("input", function () {
          el.dataset.overridden = "1";
        });
    });
    dtg.addEventListener("input", recompute);
    recompute();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initFuel();
    initEtaBySpeed();
  });
})();
