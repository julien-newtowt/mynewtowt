/*
 * Auto-pré-remplissage du form de création de leg.
 *
 * Quand l'user choisit un navire dans le select #vessel_id, on lit le
 * dict ``data-suggestions`` du form (JSON {vessel_id: {etd, pol_id,
 * port_stay_hours, from_leg_code}}) et on remplit :
 *   - input #etd        = ETD suggéré (ATA ou ETA du dernier leg
 *                          + port_stay_planned_hours)
 *   - select #departure_port_id = POD du dernier leg (continuité)
 *   - input #port_stay_planned_hours = même valeur que le leg précédent
 *
 * Conditions :
 *   - Ne touche un champ que s'il est vide (pas écraser une saisie user).
 *   - Bandeau d'info en tête de form expliquant la suggestion + bouton
 *     "Effacer" pour neutraliser.
 *
 * Si data-preselected-vessel est défini, on l'applique au chargement.
 */
(function () {
  "use strict";

  function init() {
    var form = document.getElementById("leg-form");
    if (!form) return;
    var raw = form.getAttribute("data-suggestions");
    if (!raw) return;
    var suggestions;
    try { suggestions = JSON.parse(raw); } catch (e) { return; }
    if (!suggestions || !Object.keys(suggestions).length) return;

    var selectVessel = document.getElementById("vessel_id");
    var etd = document.getElementById("etd");
    var portStay = document.getElementById("port_stay_planned_hours");
    var pol = document.getElementById("departure_port_id");
    var banner = document.getElementById("leg-suggestion-banner");
    var bannerText = document.getElementById("leg-suggestion-text");
    var dismissBtn = document.getElementById("leg-suggestion-dismiss");

    function applyFor(vesselId) {
      var s = suggestions[vesselId];
      if (!s) {
        if (banner) banner.style.display = "none";
        return;
      }
      // Ne touche que les champs vides (jamais écraser une saisie).
      if (etd && !etd.value) etd.value = s.etd;
      if (portStay && !portStay.value && s.port_stay_hours)
        portStay.value = s.port_stay_hours;
      if (pol && !pol.value && s.pol_id) {
        pol.value = String(s.pol_id);
        // Recalcule l'ETA via leg-cascade.js — UNIQUEMENT si l'ETA n'a pas
        // été saisie manuellement (dataset.auto==="off" = l'utilisateur a
        // touché le champ). Évite d'écraser une ETA voulue par l'opérateur.
        var etaEl = document.getElementById("eta");
        if (!etaEl || etaEl.dataset.auto !== "off") {
          pol.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      // Affiche le bandeau
      if (banner && bannerText) {
        var src = s.from_ata
          ? "ATA " + s.from_ata.replace("T", " ")
          : "ETA " + (s.from_eta || "").replace("T", " ");
        bannerText.textContent =
          "💡 Suggestion basée sur le leg " + s.from_leg_code +
          " (" + src + " + " + s.port_stay_hours + "h d'escale).";
        banner.style.display = "block";
      }
    }

    function clearSuggestion() {
      if (etd) etd.value = "";
      if (portStay) portStay.value = "";
      if (banner) banner.style.display = "none";
      // POL on laisse — souvent l'user veut garder le port de départ.
    }

    if (selectVessel) {
      selectVessel.addEventListener("change", function () {
        applyFor(selectVessel.value);
      });
    }
    if (dismissBtn) {
      dismissBtn.addEventListener("click", clearSuggestion);
    }

    // Apply on load if preselected
    var preselected = form.getAttribute("data-preselected-vessel");
    if (preselected && selectVessel) {
      selectVessel.value = preselected;
      applyFor(preselected);
    } else if (selectVessel && selectVessel.value) {
      // Si le navigateur a restauré une sélection (back button), apply.
      applyFor(selectVessel.value);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
