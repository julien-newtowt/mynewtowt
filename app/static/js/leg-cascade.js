/*
 * Leg form — Zone → Pays → Port en cascade, raccourcis, recherche libre,
 * aperçu d'ETA (distance × élongation / vitesse) et récapitulatif.
 *
 * Page unique « Créer un leg » (PLN-08) : le navire est choisi par boutons
 * radio (input[name=vessel_id]) ; un <select id="vessel_id"> reste supporté
 * (formulaire scénario). Les continents sont dérivés des codes ISO-2 côté
 * navigateur ; la liste des ports actifs vient de /api/v1/ports/search.
 *
 * Événement public : `document.dispatchEvent(new CustomEvent("leg:pick-port",
 * {detail: {prefix: "pol", id: 12}}))` sélectionne un port (mis en file tant
 * que la liste n'est pas chargée) — utilisé par leg-form-suggest.js.
 */
(function () {
  "use strict";

  // Approx ISO-3166-1 → continent mapping (minimal viable list)
  var CONTINENT = {
    // Europe
    FR:"Europe",GB:"Europe",IE:"Europe",DE:"Europe",ES:"Europe",PT:"Europe",
    IT:"Europe",NL:"Europe",BE:"Europe",NO:"Europe",SE:"Europe",DK:"Europe",
    FI:"Europe",IS:"Europe",PL:"Europe",GR:"Europe",HR:"Europe",CY:"Europe",
    MT:"Europe",EE:"Europe",LV:"Europe",LT:"Europe",AT:"Europe",CH:"Europe",
    RO:"Europe",BG:"Europe",
    // Americas
    US:"Amériques",CA:"Amériques",MX:"Amériques",BR:"Amériques",AR:"Amériques",
    CL:"Amériques",PE:"Amériques",CO:"Amériques",VE:"Amériques",UY:"Amériques",
    EC:"Amériques",PY:"Amériques",BO:"Amériques",CR:"Amériques",PA:"Amériques",
    CU:"Amériques",DO:"Amériques",GT:"Amériques",HN:"Amériques",JM:"Amériques",
    HT:"Amériques",GP:"Amériques",MQ:"Amériques",GF:"Amériques",
    // Africa
    MA:"Afrique",DZ:"Afrique",TN:"Afrique",EG:"Afrique",SN:"Afrique",CI:"Afrique",
    GH:"Afrique",NG:"Afrique",CM:"Afrique",GA:"Afrique",AO:"Afrique",NA:"Afrique",
    ZA:"Afrique",MZ:"Afrique",TZ:"Afrique",KE:"Afrique",DJ:"Afrique",RE:"Afrique",
    MU:"Afrique",MG:"Afrique",
    // Asia
    CN:"Asie",JP:"Asie",KR:"Asie",VN:"Asie",TH:"Asie",MY:"Asie",SG:"Asie",
    ID:"Asie",PH:"Asie",IN:"Asie",PK:"Asie",BD:"Asie",LK:"Asie",AE:"Asie",
    SA:"Asie",OM:"Asie",QA:"Asie",IL:"Asie",TR:"Asie",GE:"Asie",
    // Oceania
    AU:"Océanie",NZ:"Océanie",PG:"Océanie",FJ:"Océanie",
  };
  var DEFAULT_CONTINENT = "Autre";
  var DAY_MS = 24 * 3600 * 1000;

  function continentOf(country) {
    return CONTINENT[(country || "").toUpperCase()] || DEFAULT_CONTINENT;
  }

  var allPorts = [];   // [{id, locode, name, country, latitude, longitude}]
  var portsReady = false;
  var pendingPicks = [];

  // ── Fetch active ports once ────────────────────────────────────────
  function loadPorts() {
    return fetch("/api/v1/ports/search?limit=10000")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (rows) { allPorts = rows || []; });
  }

  function uniqueZones() {
    var s = new Set();
    allPorts.forEach(function (p) { s.add(continentOf(p.country)); });
    return Array.from(s).sort();
  }

  function countriesIn(zone) {
    var s = new Set();
    allPorts.forEach(function (p) {
      if (continentOf(p.country) === zone) s.add(p.country);
    });
    return Array.from(s).sort();
  }

  function portsIn(zone, country) {
    return allPorts.filter(function (p) {
      return (!zone || continentOf(p.country) === zone) &&
             (!country || p.country === country);
    }).sort(function (a, b) { return a.locode.localeCompare(b.locode); });
  }

  // ── Navire sélectionné (radios « boutons » ou <select> legacy) ─────
  function vesselEl() {
    var radio = document.querySelector("input[name=vessel_id]:checked");
    if (radio) return radio;
    var sel = document.getElementById("vessel_id");
    return sel && sel.selectedOptions ? sel.selectedOptions[0] : null;
  }

  // ── Cascading dropdowns for both POL & POD ─────────────────────────
  function els(prefix) {
    var q = JSON.stringify(prefix);
    return {
      zone: document.querySelector("[data-cascade-zone=" + q + "]"),
      country: document.querySelector("[data-cascade-country=" + q + "]"),
      port: document.querySelector("[data-cascade-port=" + q + "]"),
      search: document.querySelector("[data-port-search=" + q + "]"),
      results: document.querySelector("[data-port-results=" + q + "]"),
      selected: document.querySelector("[data-port-selected=" + q + "]"),
      state: document.querySelector("[data-port-state=" + q + "]"),
    };
  }

  function bindCascade(prefix) {
    var e = els(prefix);
    if (!e.zone || !e.country || !e.port) return;

    fillZone(e.zone);
    e.zone.addEventListener("change", function () {
      fillCountry(e.country, e.zone.value);
      fillPort(e.port, e.zone.value, "");
    });
    e.country.addEventListener("change", function () {
      fillPort(e.port, e.zone.value, e.country.value);
    });
    e.port.addEventListener("change", function () { renderSelected(prefix); });
    // Un port déjà présent (édition, re-rendu d'erreur) : aligner zone/pays.
    if (e.port.value) {
      var cur = allPorts.find(function (p) { return String(p.id) === e.port.value; });
      if (cur) selectPort(prefix, cur, { keepHint: true });
    }
  }

  function fillZone(el) {
    var zones = uniqueZones();
    el.innerHTML = '<option value="">— Toutes —</option>' +
      zones.map(function (z) { return '<option value="' + z + '">' + z + '</option>'; }).join("");
  }
  function fillCountry(el, zone) {
    var countries = zone ? countriesIn(zone) : [];
    el.innerHTML = '<option value="">— Tous —</option>' +
      countries.map(function (c) { return '<option value="' + c + '">' + c + '</option>'; }).join("");
  }
  function fillPort(el, zone, country) {
    var ports = portsIn(zone, country);
    var html = '<option value="">— Choisir —</option>';
    ports.slice(0, 500).forEach(function (p) {
      html += '<option value="' + p.id + '" data-locode="' + p.locode + '">' +
              p.locode + " — " + p.name + " (" + p.country + ")</option>";
    });
    el.innerHTML = html;
  }

  // ── Sélection d'un port (raccourci, recherche, suggestion) ─────────
  function selectPort(prefix, port, opts) {
    var e = els(prefix);
    if (!e.zone || !e.country || !e.port || !port) return;
    var zone = continentOf(port.country);
    e.zone.value = zone;
    fillCountry(e.country, zone);
    e.country.value = port.country;
    fillPort(e.port, zone, port.country);
    e.port.value = String(port.id);
    if (e.search) e.search.value = "";
    if (e.results) e.results.innerHTML = "";
    renderSelected(prefix, opts && opts.hint);
    updateEtaHint();
    updateRecap();
  }

  function pickByLocode(prefix, locode) {
    var port = allPorts.find(function (p) { return p.locode === locode; });
    if (!port) {
      alert("Port " + locode + " non disponible. Vérifie qu'il est actif dans /admin/ports.");
      return;
    }
    selectPort(prefix, port);
  }

  function pickById(prefix, id, hint) {
    var port = allPorts.find(function (p) { return String(p.id) === String(id); });
    if (port) selectPort(prefix, port, { hint: hint });
  }

  function renderSelected(prefix, hint) {
    var e = els(prefix);
    var port = selectedPort(prefix);
    if (e.state) e.state.textContent = port ? "Sélectionné" : "À choisir";
    if (e.state) e.state.className = "pill " + (port ? "pill-ok" : "pill-neutral");
    if (!e.selected) return;
    if (!port) { e.selected.hidden = true; return; }
    e.selected.hidden = false;
    var set = function (attr, v) {
      var n = e.selected.querySelector("[" + attr + "]");
      if (n) n.textContent = v || "";
    };
    set("data-port-selected-name", port.name);
    set("data-port-selected-locode", port.locode);
    set("data-port-selected-country", port.country);
    set("data-port-selected-hint", hint || "");
  }

  // ── Recherche libre : tous les ports, sans filtre zone / pays ───────
  function normalize(s) {
    return (s || "").toString().normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }
  function bindSearch(prefix) {
    var e = els(prefix);
    if (!e.search || !e.results) return;
    function render() {
      var q = normalize(e.search.value.trim());
      e.results.innerHTML = "";
      if (q.length < 2) return;
      var hits = allPorts.filter(function (p) {
        return normalize(p.name).indexOf(q) !== -1 || normalize(p.locode).indexOf(q) !== -1;
      }).slice(0, 8);
      if (!hits.length) {
        var none = document.createElement("div");
        none.className = "text-muted text-sm";
        none.style.padding = "8px 12px";
        none.textContent = "Aucun port actif ne correspond.";
        e.results.appendChild(none);
        return;
      }
      hits.forEach(function (p) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "port-result";
        b.setAttribute("role", "option");
        b.innerHTML = '<span><span class="mono" style="font-weight:600;color:var(--teal);">' + p.locode +
          '</span> &nbsp;<strong>' + p.name + '</strong> <span class="text-muted">' + p.country +
          ' · ' + continentOf(p.country) + '</span></span>';
        b.addEventListener("click", function () { selectPort(prefix, p); });
        e.results.appendChild(b);
      });
    }
    e.search.addEventListener("input", render);
    e.search.addEventListener("focus", render);
    e.search.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { e.results.innerHTML = ""; }
      if (ev.key === "Enter") { ev.preventDefault(); var first = e.results.querySelector(".port-result"); if (first) first.click(); }
    });
    document.addEventListener("click", function (ev) {
      if (!e.search.contains(ev.target) && !e.results.contains(ev.target)) e.results.innerHTML = "";
    });
  }

  // ── Live ETA hint (distance × elongation / speed) ──────────────────
  function selectedPort(prefix) {
    var e = els(prefix);
    if (!e.port || !e.port.value) return null;
    return allPorts.find(function (p) { return String(p.id) === e.port.value; }) || null;
  }
  function speed() {
    var tEl = document.getElementById("transit_speed_kn");
    var t = tEl ? parseFloat(tEl.value) : NaN;
    if (t > 0) return t;
    var v = vesselEl();
    return v ? parseFloat(v.dataset.defaultSpeed) || 8 : 8;
  }
  function elongation() {
    var eEl = document.getElementById("elongation_coef");
    var e = eEl ? parseFloat(eEl.value) : NaN;
    if (e > 0) return e;
    var v = vesselEl();
    return v ? parseFloat(v.dataset.defaultElongation) || 1.15 : 1.15;
  }
  function haversineNm(a, b) {
    if (!a || !b || a.latitude == null || b.latitude == null) return null;
    var p1 = a.latitude * Math.PI / 180;
    var p2 = b.latitude * Math.PI / 180;
    var dl = (b.longitude - a.longitude) * Math.PI / 180;
    var x = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * 3440.065 * Math.asin(Math.sqrt(x));
  }
  // Date seule (YYYY-MM-DD) en composantes UTC : le formulaire de planification
  // travaille à la journée — pas de dérive selon le fuseau du navigateur.
  function isoDate(d) {
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate());
  }
  function updateEtaHint() {
    var pol = selectedPort("pol");
    var pod = selectedPort("pod");
    var hint = document.getElementById("eta-hint");
    var dist = haversineNm(pol, pod);
    if (hint) hint.textContent = dist == null ? "—" : "";
    if (dist == null) { updateRecap(); return; }
    var eff = dist * elongation();
    var hours = eff / speed();
    var days = Math.ceil(hours / 24);
    if (hint) {
      hint.textContent = dist.toFixed(0) + " NM × " + elongation().toFixed(2) +
        " = " + eff.toFixed(0) + " NM @ " + speed().toFixed(1) + " kn → " + days + " j";
    }

    // Auto-fill ETA from ETD if user hasn't manually set it (durée arrondie au jour supérieur).
    var etdEl = document.getElementById("etd");
    var etaEl = document.getElementById("eta");
    if (etdEl && etaEl && etdEl.value && (!etaEl.value || etaEl.dataset.auto !== "off")) {
      var etd = new Date(etdEl.value);
      if (isNaN(etd.getTime()) || !isFinite(hours)) {
        if (etaEl.dataset.auto === "on") etaEl.value = "";
      } else {
        var eta = new Date(etd.getTime() + days * DAY_MS);
        if (!isNaN(eta.getTime())) { etaEl.value = isoDate(eta); etaEl.dataset.auto = "on"; }
      }
    }
    updateRecap();
  }

  // ── Récapitulatif (code prévisionnel, route, dates, durées) ────────
  function suggestions() {
    var form = document.getElementById("leg-form");
    if (!form) return {};
    try { return JSON.parse(form.getAttribute("data-suggestions") || "{}"); } catch (e) { return {}; }
  }
  /* Suggestion RÉELLEMENT appliquée (leg-form-suggest.js) : elle peut porter un
     autre leg de référence que le défaut du navire, choisi via « Chaîner après ».
     Le rang du code prévisionnel dépend de l'année de SON ETD. */
  function activeSuggestion() {
    var form = document.getElementById("leg-form");
    if (!form || !form.dataset.activeSuggestion) return null;
    try { return JSON.parse(form.dataset.activeSuggestion); } catch (e) { return null; }
  }
  function frDate(iso) {
    if (!iso) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : iso;
  }
  function updateRecap() {
    var codeEl = document.querySelector("[data-recap-code]");
    var textEl = document.querySelector("[data-recap-text]");
    var form = document.getElementById("leg-form");
    if (!codeEl || !textEl || !form) return;
    if (form.getAttribute("data-edit-mode")) return; // le code existant fait foi en édition
    var v = vesselEl();
    var pol = selectedPort("pol");
    var pod = selectedPort("pod");
    var etdEl = document.getElementById("etd");
    var etaEl = document.getElementById("eta");
    var stayEl = document.getElementById("port_stay_planned_days");
    var bookEl = document.getElementById("is_bookable");
    var s = activeSuggestion() || (v ? (suggestions()[v.value] || {}) : {});
    var etd = etdEl && etdEl.value ? etdEl.value : null;
    var eta = etaEl && etaEl.value ? etaEl.value : null;
    var parts = [];
    if (v) parts.push(v.parentElement && v.parentElement.querySelector(".name > span:last-child") ?
                      v.parentElement.querySelector(".name > span:last-child").textContent : (s.vessel_name || ""));
    if (pol || pod) parts.push((pol ? pol.locode : "?") + " → " + (pod ? pod.locode : "?"));
    if (etd || eta) parts.push((frDate(etd) || "?") + " → " + (frDate(eta) || "?"));
    if (etd && eta) {
      var d = Math.round((new Date(eta) - new Date(etd)) / DAY_MS);
      if (d > 0) parts.push(d + " j de mer");
    }
    if (stayEl && stayEl.value) parts.push("escale " + stayEl.value + " j");
    if (bookEl && bookEl.checked) parts.push("réservable");
    var code = "—";
    if (v && pol && pod && etd) {
      var vcode = v.dataset.vesselCode || s.vessel_code || "";
      var rank = (etd.slice(0, 4) === (s.etd || "").slice(0, 4) && s.next_rank_letter) ? s.next_rank_letter : "?";
      code = vcode + rank + pol.country.toUpperCase().slice(0, 2) + pod.country.toUpperCase().slice(0, 2) + etd.charAt(3);
    }
    codeEl.textContent = code;
    textEl.textContent = parts.length ? parts.join(" · ") : "Choisissez un navire, les ports et les dates.";
  }

  // ── Init ───────────────────────────────────────────────────────────
  function init() {
    document.addEventListener("leg:pick-port", function (ev) {
      var d = ev.detail || {};
      if (portsReady) pickById(d.prefix, d.id, d.hint); else pendingPicks.push(d);
    });

    loadPorts().then(function () {
      portsReady = true;
      bindCascade("pol");
      bindCascade("pod");
      bindSearch("pol");
      bindSearch("pod");

      document.querySelectorAll("[data-shortcut]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          pickByLocode(btn.dataset.shortcut, btn.dataset.locode);
        });
      });

      ["pol", "pod"].forEach(function (p) {
        var el = document.querySelector("[data-cascade-port=" + JSON.stringify(p) + "]");
        if (el) el.addEventListener("change", updateEtaHint);
      });
      ["transit_speed_kn", "elongation_coef", "etd"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener("change", updateEtaHint);
      });
      document.querySelectorAll("input[name=vessel_id]").forEach(function (r) {
        r.addEventListener("change", updateEtaHint);
      });
      var vsel = document.getElementById("vessel_id");
      if (vsel) vsel.addEventListener("change", updateEtaHint);
      var etaEl = document.getElementById("eta");
      if (etaEl) etaEl.addEventListener("input", function (e) { e.target.dataset.auto = "off"; updateRecap(); });
      ["port_stay_planned_days", "is_bookable"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener("change", updateRecap);
      });

      pendingPicks.splice(0).forEach(function (d) { pickById(d.prefix, d.id, d.hint); });
      updateEtaHint();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
