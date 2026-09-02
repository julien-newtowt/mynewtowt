/*
 * Leg form — Zone → Pays → Port en cascade, raccourcis, recherche libre,
 * aperçu d'ETA (distance × élongation / vitesse) et récapitulatif.
 *
 * Page unique « Créer un leg » (PLN-08) : le navire est choisi par boutons
 * radio (input[name=vessel_id]) ; un <select id="vessel_id"> reste supporté
 * (formulaire scénario).
 *
 * ── Pourquoi tout passe par le serveur ────────────────────────────────────
 * Ce fichier rapatriait le référentiel entier (`/api/v1/ports/search?limit=10000`)
 * puis filtrait dans le navigateur. Passé 10 000 ports en base, la requête
 * **tronquait silencieusement** : triée par pays, tout ce qui suit `JP`
 * disparaissait — 123 pays, dont le Viêt Nam (Da Nang `VNDAD` introuvable),
 * les Pays-Bas, les États-Unis, La Réunion… La cascade Zone/Pays/Port ET la
 * recherche libre étant dérivées de ce même payload, les filtres étaient
 * incomplets sans que rien ne le signale.
 *
 * Désormais : les zones et pays viennent de `/api/v1/ports/countries`
 * (quelques centaines d'octets, zone calculée par `services.geo.region_of`,
 * couverture ISO-3166 complète — plus de carte de continents codée en dur
 * ici) ; les ports d'un pays et la recherche libre sont **requêtés au
 * serveur** ; un port désigné par son id est lu via `/api/v1/ports/{id}`.
 *
 * Événement public : `document.dispatchEvent(new CustomEvent("leg:pick-port",
 * {detail: {prefix: "pol", id: 12}}))` sélectionne un port (mis en file tant
 * que la liste des pays n'est pas chargée) — utilisé par leg-form-suggest.js.
 */
(function () {
  "use strict";

  var DAY_MS = 24 * 3600 * 1000;
  var PORTS_PER_COUNTRY = 500;   // plafond serveur de /ports/search
  var SEARCH_LIMIT = 8;
  var SEARCH_DEBOUNCE_MS = 220;

  var countries = [];            // [{country, zone, port_count}]
  var zoneByCountry = {};        // "VN" -> "Asie"
  var ready = false;
  var pendingPicks = [];
  var portCache = {};            // id -> {id, locode, name, country, lat, lon}

  function api(path) {
    return fetch(path, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  function cache(port) {
    if (port && port.id != null) portCache[String(port.id)] = port;
    return port;
  }

  function zoneOf(country) {
    return zoneByCountry[(country || "").toUpperCase()] || "Autre";
  }

  // ── Pays + zones : un seul appel, quelques centaines d'octets ──────
  function loadCountries() {
    return api("/api/v1/ports/countries").then(function (rows) {
      countries = rows || [];
      zoneByCountry = {};
      countries.forEach(function (c) { zoneByCountry[c.country] = c.zone; });
    });
  }

  function uniqueZones() {
    var seen = {};
    var out = [];
    // L'API trie déjà par zone (ordre métier) puis pays : on conserve cet ordre.
    countries.forEach(function (c) {
      if (!seen[c.zone]) { seen[c.zone] = true; out.push(c.zone); }
    });
    return out;
  }

  function countriesIn(zone) {
    return countries
      .filter(function (c) { return !zone || c.zone === zone; })
      .map(function (c) { return c; });
  }

  // ── Ports d'un pays : requête serveur, jamais la table entière ─────
  function fetchPortsOfCountry(country) {
    if (!country) return Promise.resolve([]);
    return api("/api/v1/ports/search?limit=" + PORTS_PER_COUNTRY +
               "&country=" + encodeURIComponent(country))
      .then(function (rows) { (rows || []).forEach(cache); return rows || []; });
  }

  function fetchPortById(id) {
    var hit = portCache[String(id)];
    if (hit) return Promise.resolve(hit);
    return api("/api/v1/ports/" + encodeURIComponent(id)).then(cache);
  }

  function fetchPortByLocode(locode) {
    return api("/api/v1/ports/search?limit=10&q=" + encodeURIComponent(locode))
      .then(function (rows) {
        var exact = (rows || []).filter(function (p) { return p.locode === locode; });
        return exact.length ? cache(exact[0]) : null;
      });
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
    // Les deux selects suivants sont rendus vides par le gabarit : sans ça, le
    // champ Port est un menu déroulant vide qui n'explique pas ce qu'il attend
    // (et le `required` du formulaire produit un message opaque).
    fillCountry(e.country, "");
    if (!e.port.value) resetPort(e.port, "Choisissez une zone puis un pays");
    e.zone.addEventListener("change", function () {
      fillCountry(e.country, e.zone.value);
      // Pas de pays choisi = pas de liste : une zone entière peut porter
      // plusieurs milliers de ports, et les déverser n'aide personne. La
      // recherche libre couvre le cas « je ne sais pas dans quel pays ».
      resetPort(e.port, e.zone.value ? "Choisissez un pays" : "— Choisir —");
    });
    e.country.addEventListener("change", function () {
      fillPort(e.port, e.country.value);
    });
    e.port.addEventListener("change", function () { renderSelected(prefix); });
    // Un port déjà présent (édition, re-rendu d'erreur) : aligner zone/pays.
    if (e.port.value) {
      fetchPortById(e.port.value).then(function (cur) {
        if (cur) selectPort(prefix, cur, { keepHint: true });
      });
    }
  }

  function fillZone(el) {
    el.innerHTML = '<option value="">— Toutes —</option>' +
      uniqueZones().map(function (z) {
        return '<option value="' + z + '">' + z + "</option>";
      }).join("");
  }

  function fillCountry(el, zone) {
    var rows = zone ? countriesIn(zone) : [];
    el.innerHTML = '<option value="">— Tous —</option>' +
      rows.map(function (c) {
        return '<option value="' + c.country + '">' + c.country +
               " (" + c.port_count + ")</option>";
      }).join("");
  }

  function resetPort(el, label) {
    el.innerHTML = '<option value="">' + label + "</option>";
  }

  function fillPort(el, country) {
    if (!country) { resetPort(el, "Choisissez un pays"); return Promise.resolve([]); }
    resetPort(el, "Chargement…");
    return fetchPortsOfCountry(country).then(function (ports) {
      var html = '<option value="">— Choisir —</option>';
      ports.forEach(function (p) {
        html += '<option value="' + p.id + '" data-locode="' + p.locode + '">' +
                p.locode + " — " + p.name + " (" + p.country + ")</option>";
      });
      // Le plafond serveur est atteint : le dire, plutôt que laisser croire
      // que la liste est exhaustive (cf. la troncature silencieuse d'avant).
      if (ports.length >= PORTS_PER_COUNTRY) {
        html += '<option value="" disabled>… liste tronquée à ' + PORTS_PER_COUNTRY +
                " — affinez par la recherche</option>";
      }
      el.innerHTML = html;
      return ports;
    });
  }

  // ── Sélection d'un port (raccourci, recherche, suggestion) ─────────
  function selectPort(prefix, port, opts) {
    var e = els(prefix);
    if (!e.zone || !e.country || !e.port || !port) return Promise.resolve();
    cache(port);
    var zone = zoneOf(port.country);
    e.zone.value = zone;
    fillCountry(e.country, zone);
    e.country.value = port.country;
    return fillPort(e.port, port.country).then(function (ports) {
      // Le port peut être hors du lot renvoyé (pays très fourni, plafond
      // atteint) : on l'ajoute pour que la sélection soit toujours possible.
      var known = ports.some(function (p) { return String(p.id) === String(port.id); });
      if (!known) {
        var opt = document.createElement("option");
        opt.value = String(port.id);
        opt.setAttribute("data-locode", port.locode);
        opt.textContent = port.locode + " — " + port.name + " (" + port.country + ")";
        e.port.insertBefore(opt, e.port.firstChild ? e.port.firstChild.nextSibling : null);
      }
      e.port.value = String(port.id);
      if (e.search) e.search.value = "";
      if (e.results) e.results.innerHTML = "";
      renderSelected(prefix, opts && opts.hint);
      updateEtaHint();
      updateRecap();
    });
  }

  function pickByLocode(prefix, locode) {
    fetchPortByLocode(locode).then(function (port) {
      if (!port) {
        alert("Port " + locode + " non disponible. Vérifie qu'il est actif dans /admin/ports.");
        return;
      }
      selectPort(prefix, port);
    });
  }

  function pickById(prefix, id, hint) {
    fetchPortById(id).then(function (port) {
      if (port) selectPort(prefix, port, { hint: hint });
    });
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

  // ── Recherche libre : requêtée au SERVEUR, tout le référentiel ──────
  function bindSearch(prefix) {
    var e = els(prefix);
    if (!e.search || !e.results) return;
    var timer = null;
    var seq = 0;

    function message(text) {
      e.results.innerHTML = "";
      var n = document.createElement("div");
      n.className = "text-muted text-sm";
      n.style.padding = "8px 12px";
      n.textContent = text;
      e.results.appendChild(n);
    }

    function run() {
      var q = e.search.value.trim();
      if (q.length < 2) { e.results.innerHTML = ""; return; }
      var mine = ++seq;
      api("/api/v1/ports/search?limit=" + SEARCH_LIMIT + "&q=" + encodeURIComponent(q))
        .then(function (rows) {
          // Réponse d'une frappe précédente arrivée en retard : on l'ignore.
          if (mine !== seq) return;
          if (rows === null) { message("Recherche indisponible — réessayez."); return; }
          if (!rows.length) { message("Aucun port actif ne correspond."); return; }
          e.results.innerHTML = "";
          rows.forEach(function (p) {
            cache(p);
            var b = document.createElement("button");
            b.type = "button";
            b.className = "port-result";
            b.setAttribute("role", "option");
            b.innerHTML = '<span><span class="mono" style="font-weight:600;color:var(--teal);">' +
              p.locode + '</span> &nbsp;<strong>' + p.name + '</strong> <span class="text-muted">' +
              p.country + " · " + zoneOf(p.country) + "</span></span>";
            b.addEventListener("click", function () { selectPort(prefix, p); });
            e.results.appendChild(b);
          });
        });
    }

    function schedule() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(run, SEARCH_DEBOUNCE_MS);
    }

    e.search.addEventListener("input", schedule);
    e.search.addEventListener("focus", schedule);
    e.search.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { e.results.innerHTML = ""; }
      if (ev.key === "Enter") {
        ev.preventDefault();
        var first = e.results.querySelector(".port-result");
        if (first) first.click();
      }
    });
    document.addEventListener("click", function (ev) {
      if (!e.search.contains(ev.target) && !e.results.contains(ev.target)) e.results.innerHTML = "";
    });
  }

  // ── Live ETA hint (distance × elongation / speed) ──────────────────
  function selectedPort(prefix) {
    var e = els(prefix);
    if (!e.port || !e.port.value) return null;
    // Le cache est alimenté par tout chemin de sélection (liste par pays,
    // recherche, raccourci, suggestion) : l'aperçu d'ETA a besoin des
    // coordonnées, et on ne détient plus le référentiel complet.
    return portCache[e.port.value] || null;
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
      if (ready) pickById(d.prefix, d.id, d.hint); else pendingPicks.push(d);
    });

    loadCountries().then(function () {
      ready = true;
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
