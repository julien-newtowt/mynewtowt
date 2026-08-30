/*
 * Densité d'affichage des tableaux — reprise UX Phase 3
 * (docs/design/03-reprise-ux-legacy.md §4 Phase 3).
 *
 * Préférence PAR NAVIGATEUR (localStorage `towt-density`), volontairement
 * PAS persistée en base : pas d'écran de configuration serveur nécessaire,
 * ne survit pas au changement de poste — un simple confort local.
 *
 * Applique la classe `density-cosy` sur <body> (cf. kairos.css
 * `body.density-cosy .data-table th/td`) au chargement, et expose un
 * bouton bascule `#density-toggle` (icône topbar) — pattern IIFE identique
 * à sidebar.js, aucun handler inline (CSP stricte).
 */
(function () {
  "use strict";

  var KEY = "towt-density";
  var CLASS = "density-cosy";

  function isCosy() {
    try {
      return localStorage.getItem(KEY) === "cosy";
    } catch (e) {
      return false; // quota / navigation privée
    }
  }

  function apply(cosy) {
    document.body.classList.toggle(CLASS, cosy);
  }

  function toggle() {
    var next = !document.body.classList.contains(CLASS);
    apply(next);
    try {
      localStorage.setItem(KEY, next ? "cosy" : "compact");
    } catch (e) {
      /* quota / navigation privée — la bascule reste effective pour cette page */
    }
  }

  function bind() {
    var btn = document.getElementById("density-toggle");
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      toggle();
    });
  }

  function init() {
    apply(isCosy());
    bind();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
