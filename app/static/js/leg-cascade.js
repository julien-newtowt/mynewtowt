/*
 * Leg form — dynamic Zone → Country → Port cascade + shortcuts +
 * live ETA preview computed from speed × elongation.
 *
 * Continents are derived from ISO-2 country codes in the browser
 * (no extra endpoint). Country lists come from /api/v1/ports/search
 * (active ports only).
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
    HT:"Amériques",
    // Africa
    MA:"Afrique",DZ:"Afrique",TN:"Afrique",EG:"Afrique",SN:"Afrique",CI:"Afrique",
    GH:"Afrique",NG:"Afrique",CM:"Afrique",GA:"Afrique",AO:"Afrique",NA:"Afrique",
    ZA:"Afrique",MZ:"Afrique",TZ:"Afrique",KE:"Afrique",DJ:"Afrique",
    // Asia
    CN:"Asie",JP:"Asie",KR:"Asie",VN:"Asie",TH:"Asie",MY:"Asie",SG:"Asie",
    ID:"Asie",PH:"Asie",IN:"Asie",PK:"Asie",BD:"Asie",LK:"Asie",AE:"Asie",
    SA:"Asie",OM:"Asie",QA:"Asie",IL:"Asie",TR:"Asie",GE:"Asie",
    // Oceania
    AU:"Océanie",NZ:"Océanie",PG:"Océanie",FJ:"Océanie",
  };
  var DEFAULT_CONTINENT = "Autre";

  function continentOf(country) {
    return CONTINENT[(country || "").toUpperCase()] || DEFAULT_CONTINENT;
  }

  var allPorts = [];   // [{id, locode, name, country}]

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

  // ── Cascading dropdowns for both POL & POD ─────────────────────────
  function bindCascade(prefix) {
    var zoneEl    = document.querySelector("[data-cascade-zone="    + JSON.stringify(prefix) + "]");
    var countryEl = document.querySelector("[data-cascade-country=" + JSON.stringify(prefix) + "]");
    var portEl    = document.querySelector("[data-cascade-port="    + JSON.stringify(prefix) + "]");
    if (!zoneEl || !countryEl || !portEl) return;

    fillZone(zoneEl);
    zoneEl.addEventListener("change", function () {
      fillCountry(countryEl, zoneEl.value);
      fillPort(portEl, zoneEl.value, "");
    });
    countryEl.addEventListener("change", function () {
      fillPort(portEl, zoneEl.value, countryEl.value);
    });
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

  // ── Shortcuts (Fécamp / São Sebastião) ─────────────────────────────
  function pickByLocode(prefix, locode) {
    var port = allPorts.find(function (p) { return p.locode === locode; });
    if (!port) {
      alert("Port " + locode + " non disponible. Vérifie qu'il est actif dans /admin/ports.");
      return;
    }
    var zone = continentOf(port.country);
    var zoneEl    = document.querySelector("[data-cascade-zone="    + JSON.stringify(prefix) + "]");
    var countryEl = document.querySelector("[data-cascade-country=" + JSON.stringify(prefix) + "]");
    var portEl    = document.querySelector("[data-cascade-port="    + JSON.stringify(prefix) + "]");
    zoneEl.value = zone;
    fillCountry(countryEl, zone);
    countryEl.value = port.country;
    fillPort(portEl, zone, port.country);
    portEl.value = String(port.id);
    updateEtaHint();
  }

  // ── Live ETA hint (distance × elongation / speed) ──────────────────
  function selectedPort(prefix) {
    var portEl = document.querySelector("[data-cascade-port=" + JSON.stringify(prefix) + "]");
    if (!portEl || !portEl.value) return null;
    return allPorts.find(function (p) { return String(p.id) === portEl.value; }) || null;
  }
  function speed() {
    var t = parseFloat(document.getElementById("transit_speed_kn").value);
    if (t > 0) return t;
    var v = document.getElementById("vessel_id").selectedOptions[0];
    return v ? parseFloat(v.dataset.defaultSpeed) || 8 : 8;
  }
  function elongation() {
    var e = parseFloat(document.getElementById("elongation_coef").value);
    if (e > 0) return e;
    var v = document.getElementById("vessel_id").selectedOptions[0];
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
  function isoLocal(d) {
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  function updateEtaHint() {
    var pol = selectedPort("pol");
    var pod = selectedPort("pod");
    var hint = document.getElementById("eta-hint");
    if (!hint) return;
    var dist = haversineNm(pol, pod);
    if (dist == null) { hint.textContent = "—"; return; }
    var eff = dist * elongation();
    var hours = eff / speed();
    hint.textContent = dist.toFixed(0) + " NM × " + elongation().toFixed(2) +
                       " = " + eff.toFixed(0) + " NM @ " + speed().toFixed(1) + " kn → " +
                       (hours / 24).toFixed(1) + " j (" + hours.toFixed(0) + " h)";

    // Auto-fill ETA from ETD if user hasn't manually set it.
    var etdEl = document.getElementById("etd");
    var etaEl = document.getElementById("eta");
    if (!etdEl || !etaEl) return;
    if (etdEl.value && (!etaEl.value || etaEl.dataset.auto !== "off")) {
      var etd = new Date(etdEl.value);
      // Garde-fou : ETD invalide (champ vidé/partiel) ⇒ ne pas écrire un
      // "NaN" dans l'ETA. On efface l'ETA auto-remplie au lieu de la corrompre.
      if (isNaN(etd.getTime()) || !isFinite(hours)) {
        if (etaEl.dataset.auto === "on") etaEl.value = "";
        return;
      }
      var eta = new Date(etd.getTime() + hours * 3600 * 1000);
      if (isNaN(eta.getTime())) return;
      etaEl.value = isoLocal(eta);
      etaEl.dataset.auto = "on";
    }
  }

  // ── Init ───────────────────────────────────────────────────────────
  function init() {
    loadPorts().then(function () {
      bindCascade("pol");
      bindCascade("pod");

      document.querySelectorAll("[data-shortcut]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          pickByLocode(btn.dataset.shortcut, btn.dataset.locode);
        });
      });

      ["pol", "pod"].forEach(function (p) {
        var el = document.querySelector("[data-cascade-port=" + JSON.stringify(p) + "]");
        if (el) el.addEventListener("change", updateEtaHint);
      });
      ["vessel_id", "transit_speed_kn", "elongation_coef", "etd"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener("change", updateEtaHint);
      });
      document.getElementById("eta")?.addEventListener("input", function (e) {
        e.target.dataset.auto = "off";   // user touched ETA → stop auto-filling
      });
      updateEtaHint();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
