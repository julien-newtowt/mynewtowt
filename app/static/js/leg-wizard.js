/*
 * Wizard de création/édition de leg — démarche step-by-step (finding #7).
 *
 * Découpe le formulaire #leg-form en étapes (.wizard-step[data-step]) :
 *   1. Navire → 2. Origine/destination → 3. ETA/ETD → 4. Réservation.
 *
 * Affiche une étape à la fois, avec Précédent/Suivant et une barre de
 * progression. Valide les champs requis de l'étape courante (HTML5
 * checkValidity) avant d'avancer. La soumission réelle reste un POST
 * classique du form complet (back-end inchangé). CSP-safe : externe,
 * aucun inline.
 *
 * En mode édition (data-wizard-edit="1") on peut aussi afficher toutes
 * les étapes d'un coup via le bouton "Tout afficher".
 */
(function () {
  "use strict";

  function init() {
    var form = document.getElementById("leg-form");
    if (!form) return;
    var steps = Array.prototype.slice.call(form.querySelectorAll(".wizard-step"));
    if (steps.length < 2) return;  // pas de wizard si une seule étape

    var progress = document.getElementById("wizard-progress");
    var dots = progress
      ? Array.prototype.slice.call(progress.querySelectorAll("[data-step-dot]"))
      : [];
    var prevBtn = form.querySelector("[data-wizard-prev]");
    var nextBtn = form.querySelector("[data-wizard-next]");
    var submitBtn = form.querySelector("[data-wizard-submit]");
    var current = 0;

    function render() {
      steps.forEach(function (s, i) { s.hidden = (i !== current); });
      dots.forEach(function (d, i) {
        d.classList.toggle("is-active", i === current);
        d.classList.toggle("is-done", i < current);
      });
      if (prevBtn) prevBtn.style.visibility = current === 0 ? "hidden" : "visible";
      var last = current === steps.length - 1;
      if (nextBtn) nextBtn.style.display = last ? "none" : "";
      if (submitBtn) submitBtn.style.display = last ? "" : "none";
    }

    // Valide uniquement les champs de l'étape courante (HTML5).
    function stepValid() {
      var fields = steps[current].querySelectorAll("input, select, textarea");
      for (var i = 0; i < fields.length; i++) {
        if (!fields[i].checkValidity()) {
          fields[i].reportValidity();
          return false;
        }
      }
      return true;
    }

    if (nextBtn) {
      nextBtn.addEventListener("click", function (e) {
        e.preventDefault();
        if (!stepValid()) return;
        if (current < steps.length - 1) { current++; render(); window.scrollTo(0, 0); }
      });
    }
    if (prevBtn) {
      prevBtn.addEventListener("click", function (e) {
        e.preventDefault();
        if (current > 0) { current--; render(); window.scrollTo(0, 0); }
      });
    }
    // Clic direct sur un point de progression déjà franchi.
    dots.forEach(function (d, i) {
      d.addEventListener("click", function () {
        if (i <= current || i < current) { current = i; render(); }
      });
    });

    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
