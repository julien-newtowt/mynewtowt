/*
 * Pré-remplissage du formulaire « Créer un leg » depuis la séquence du navire.
 *
 * Quand l'utilisateur choisit un navire (boutons radio input[name=vessel_id],
 * ou <select id="vessel_id"> legacy), on lit le dict ``data-suggestions`` du
 * form (JSON {vessel_id: {etd, pol_id, port_stay_days, from_leg_code, …}},
 * calculé côté serveur — planning_router._new_leg_suggestions) et on remplit :
 *   - #etd                      = ETD suggéré (ATA ou ETA du dernier leg
 *                                 + escale, jour ouvré du port)
 *   - port de départ (POL)      = POD du dernier leg (continuité), via
 *                                 l'événement `leg:pick-port` (leg-cascade.js)
 *   - #port_stay_planned_days   = même escale que le leg précédent
 *
 * Conditions : ne touche un champ que s'il est vide (jamais écraser une
 * saisie), sauf au changement de navire où la suggestion précédente est
 * remplacée. Bandeau « Séquence » explicatif + bouton « Effacer ».
 * Sans effet en mode édition (data-edit-mode).
 */
(function () {
  "use strict";

  function frDate(iso) {
    if (!iso) return "";
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : iso;
  }

  function init() {
    var form = document.getElementById("leg-form");
    if (!form || form.getAttribute("data-edit-mode")) return;
    var raw = form.getAttribute("data-suggestions");
    if (!raw) return;
    var suggestions;
    try { suggestions = JSON.parse(raw); } catch (e) { return; }
    if (!suggestions || !Object.keys(suggestions).length) return;

    var etd = document.getElementById("etd");
    var eta = document.getElementById("eta");
    var portStay = document.getElementById("port_stay_planned_days");
    var banner = document.getElementById("leg-suggestion-banner");
    var bannerText = document.getElementById("leg-suggestion-text");
    var dismissBtn = document.getElementById("leg-suggestion-dismiss");
    var appliedFor = null;   // navire dont la suggestion est en place

    function applyFor(vesselId, force) {
      var s = suggestions[vesselId];
      if (!s || s.no_legs || !s.etd) {
        if (banner) banner.style.display = "none";
        appliedFor = null;
        return;
      }
      // Changement de navire : la suggestion précédente est remplacée.
      var replace = force || (appliedFor !== null && appliedFor !== vesselId);
      if (etd && (replace || !etd.value)) { etd.value = s.etd; if (eta) { eta.value = ""; eta.dataset.auto = "on"; } }
      if (portStay && (replace || !portStay.value) && s.port_stay_days) portStay.value = s.port_stay_days;
      if (s.pol_id) {
        document.dispatchEvent(new CustomEvent("leg:pick-port", {
          detail: {
            prefix: "pol",
            id: s.pol_id,
            hint: "Port d'arrivée du leg précédent " + s.from_leg_code + " — continuité géographique.",
          },
        }));
      }
      appliedFor = vesselId;
      if (banner && bannerText) {
        var ref = s.from_ata ? "ATA " + frDate(s.from_ata) : "ETA " + frDate(s.from_eta);
        bannerText.textContent =
          "Le nouveau leg suit " + s.from_leg_code + " (" + (s.from_pol_locode || "?") + " → " +
          (s.from_pod_locode || "?") + ", " + ref + "). Départ de " + (s.from_pod_name || s.from_pod_locode) +
          " après " + s.port_stay_days + " j d'escale : ETD suggéré " + frDate(s.etd) + ". Tout reste modifiable.";
        banner.style.display = "flex";
        banner.style.alignItems = "center";
        banner.style.flexWrap = "wrap";
      }
    }

    function clearSuggestion() {
      if (etd) etd.value = "";
      if (eta) { eta.value = ""; eta.dataset.auto = "on"; }
      if (portStay) portStay.value = "";
      if (banner) banner.style.display = "none";
      appliedFor = null;
      // POL on laisse — souvent l'user veut garder le port de départ.
    }

    document.querySelectorAll("input[name=vessel_id]").forEach(function (r) {
      r.addEventListener("change", function () { if (r.checked) applyFor(r.value, true); });
    });
    var selectVessel = document.getElementById("vessel_id");
    if (selectVessel) {
      selectVessel.addEventListener("change", function () { applyFor(selectVessel.value, true); });
    }
    if (dismissBtn) dismissBtn.addEventListener("click", clearSuggestion);

    // Navire présélectionné (lien « + leg » depuis le Gantt) ou restauré par le navigateur.
    var preselected = form.getAttribute("data-preselected-vessel");
    var checked = document.querySelector("input[name=vessel_id]:checked");
    if (preselected) {
      var r = document.querySelector('input[name=vessel_id][value="' + preselected + '"]');
      if (r) r.checked = true;
      applyFor(preselected, false);
    } else if (checked) {
      applyFor(checked.value, false);
    } else if (selectVessel && selectVessel.value) {
      applyFor(selectVessel.value, false);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
