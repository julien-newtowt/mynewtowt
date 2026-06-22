/*
 * Gantt provisoire (scénarios) — glisser-déposer des traversées.
 *
 * Sur la vue Gantt d'un scénario de planification provisoire, permet de :
 *   - déplacer une barre (corps) → décale ETD et ETA du même delta ;
 *   - tirer le bord gauche → décale le DÉBUT (ETD) ;
 *   - tirer le bord droit → décale la FIN (ETA, durée d'escale/transit).
 *
 * Conversion pixels→dates via la fenêtre [window-start, window-end] (epoch ms
 * UTC) portée par le conteneur. À la fin du glissement, POST vers
 * /planning/scenarios/{id}/legs/{legId}/move (CSRF x-csrf-token), puis reload.
 * CSP-safe : fichier externe, aucun inline.
 */
(function () {
  "use strict";

  var EDGE_PX = 8; // zone de préhension des bords (resize)
  var MIN_PCT = 0.3; // largeur minimale d'une barre
  var HOUR_MS = 3600 * 1000;

  function getCsrf() {
    var m = document.cookie.split("; ").find(function (r) {
      return r.indexOf("towt_csrf=") === 0;
    });
    return m ? m.split("=")[1] : "";
  }

  function snapHour(ms) {
    return Math.round(ms / HOUR_MS) * HOUR_MS;
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  // Instant UTC → "YYYY-MM-DDTHH:MM" (le backend interprète le naïf en UTC).
  function toIsoUtc(ms) {
    var d = new Date(ms);
    return (
      d.getUTCFullYear() +
      "-" +
      pad(d.getUTCMonth() + 1) +
      "-" +
      pad(d.getUTCDate()) +
      "T" +
      pad(d.getUTCHours()) +
      ":" +
      pad(d.getUTCMinutes())
    );
  }

  function init() {
    var root = document.querySelector("[data-scenario-gantt]");
    if (!root) return;
    var scenarioId = root.getAttribute("data-scenario-id");
    var winStart = parseInt(root.getAttribute("data-window-start"), 10);
    var winEnd = parseInt(root.getAttribute("data-window-end"), 10);
    if (!isFinite(winStart) || !isFinite(winEnd) || winEnd <= winStart) return;
    var span = winEnd - winStart;

    root.querySelectorAll("[data-draggable-bar]").forEach(function (bar) {
      setupBar(bar, scenarioId, winStart, span);
    });
  }

  function setupBar(bar, scenarioId, winStart, span) {
    var lane = bar.closest(".gantt-lane");
    if (!lane) return;
    var mode = null;
    var startX = 0;
    var origLeft = 0;
    var origWidth = 0;
    var moved = false;
    var suppressClick = false;

    bar.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      var rect = bar.getBoundingClientRect();
      var offX = e.clientX - rect.left;
      if (offX < EDGE_PX) mode = "start";
      else if (offX > rect.width - EDGE_PX) mode = "end";
      else mode = "move";
      startX = e.clientX;
      origLeft = parseFloat(bar.style.left) || 0;
      origWidth = parseFloat(bar.style.width) || 0;
      moved = false;
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });

    // Empêche la navigation (lien d'édition) après un vrai glissement.
    bar.addEventListener("click", function (e) {
      if (suppressClick) {
        e.preventDefault();
        e.stopPropagation();
        suppressClick = false;
      }
    });

    function onMove(e) {
      var laneW = lane.getBoundingClientRect().width || 1;
      var dxPct = ((e.clientX - startX) / laneW) * 100;
      if (Math.abs(e.clientX - startX) > 3) moved = true;
      if (mode === "move") {
        bar.style.left = origLeft + dxPct + "%";
      } else if (mode === "start") {
        bar.style.left = origLeft + dxPct + "%";
        bar.style.width = Math.max(origWidth - dxPct, MIN_PCT) + "%";
      } else if (mode === "end") {
        bar.style.width = Math.max(origWidth + dxPct, MIN_PCT) + "%";
      }
    }

    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (!moved) {
        mode = null;
        return; // simple clic → laisse le lien d'édition s'ouvrir
      }
      suppressClick = true;
      var leftPct = parseFloat(bar.style.left) || 0;
      var widthPct = parseFloat(bar.style.width) || 0;
      var etdMs = snapHour(winStart + (leftPct / 100) * span);
      var etaMs = snapHour(winStart + ((leftPct + widthPct) / 100) * span);
      if (etaMs - etdMs < HOUR_MS) etaMs = etdMs + HOUR_MS;
      save(etdMs, etaMs);
      mode = null;
    }

    function save(etdMs, etaMs) {
      bar.style.opacity = "0.5";
      var body = new URLSearchParams();
      body.set("etd", toIsoUtc(etdMs));
      body.set("eta", toIsoUtc(etaMs));
      body.set("_csrf", getCsrf());
      fetch(
        "/planning/scenarios/" +
          scenarioId +
          "/legs/" +
          bar.getAttribute("data-leg-id") +
          "/move",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            "x-csrf-token": getCsrf(),
          },
          body: body.toString(),
        }
      )
        .then(function (r) {
          return r.json().catch(function () {
            return { ok: false };
          });
        })
        .then(function (d) {
          if (!d.ok && d.error) {
            window.alert("Déplacement refusé : " + d.error);
          }
          window.location.reload();
        })
        .catch(function () {
          window.location.reload();
        });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
