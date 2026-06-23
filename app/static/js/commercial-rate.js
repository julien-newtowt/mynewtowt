/*
 * commercial-rate.js — COM-07.
 *
 * Applique le tarif grille recommandé (fragment HTMX du devis live) au champ
 * « Tarif (€/palette) » du formulaire de commande, sur clic du bouton
 * [data-apply-rate]. Le fragment étant injecté par HTMX, on délègue l'écoute
 * au document (capture des clics même sur contenu ajouté après chargement).
 *
 * CSP-safe : fichier externe, aucun script inline.
 */
(function () {
  "use strict";

  document.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-apply-rate]") : null;
    if (!btn) return;
    var rate = btn.getAttribute("data-apply-rate");
    var targetId = btn.getAttribute("data-apply-target") || "rate_per_palette_eur";
    var input = document.getElementById(targetId);
    if (input && rate != null) {
      input.value = rate;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
})();
