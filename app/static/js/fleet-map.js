/*
 * Fleet map — initialise une carte MapLibre et place les navires en marqueurs.
 *
 * Utilisable sur toutes les pages qui présentent un conteneur :
 *   <div class="js-fleet-map"
 *        data-maptiler-token="..."
 *        data-vessels='[{"name":"Anemos","code":"1","lat":...,"lon":...,"sog":..., "recorded_at":"..."}]'
 *        data-center="[-30,40]" data-zoom="2"></div>
 *
 * Lit la liste des navires depuis data-vessels, ne place QUE ceux qui ont
 * lat+lon non nuls, affiche un toast "aucune position" sinon.
 *
 * Compatible CSP strict (no inline script) — chargé via <script src="…" defer>
 * dans la page.
 */
(function () {
  "use strict";

  function initMap(el) {
    if (!el || el.dataset.fleetMapBound === "1") return;
    if (typeof window.maplibregl === "undefined") {
      // MapLibre n'est pas encore chargé — réessaie après defer load
      return false;
    }
    el.dataset.fleetMapBound = "1";

    var token = el.dataset.maptilerToken || "";
    var vessels;
    try { vessels = JSON.parse(el.dataset.vessels || "[]"); }
    catch (e) { vessels = []; }

    var center = [-30, 40];
    var zoom = 2;
    try { if (el.dataset.center) center = JSON.parse(el.dataset.center); } catch (e) {}
    try { if (el.dataset.zoom) zoom = parseFloat(el.dataset.zoom); } catch (e) {}

    // Style de fond : par défaut "outdoor-v2" (MapTiler Outdoor — relief,
    // terrain, lisible terre + mer, charte compagnie). Surchargeable via
    // data-map-style="streets-v2 | ocean | basic-v2 | dataviz-dark | ...".
    var mapStyle = el.dataset.mapStyle || "outdoor-v2";
    var style = token
      ? "https://api.maptiler.com/maps/" + encodeURIComponent(mapStyle) +
        "/style.json?key=" + encodeURIComponent(token)
      : "https://demotiles.maplibre.org/style.json";

    var map = new window.maplibregl.Map({
      container: el,
      style: style,
      center: center,
      zoom: zoom,
      attributionControl: { compact: true },
    });
    map.addControl(new window.maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("load", function () {
      var withPos = vessels.filter(function (v) {
        return typeof v.lat === "number" && typeof v.lon === "number";
      });

      withPos.forEach(function (v) {
        var marker = document.createElement("div");
        marker.style.cssText = (
          "width:34px;height:34px;border-radius:50%;background:#0D5966;" +
          "color:#fff;display:flex;align-items:center;justify-content:center;" +
          "font-family:'JetBrains Mono',monospace;font-weight:700;font-size:11px;" +
          "border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.4);"
        );
        marker.textContent = v.code || "";
        var html = "<strong>" + (v.name || "") + "</strong>";
        if (v.code) {
          html += "<br><span style=\"font-family:monospace\">" + v.code + "</span>";
        }
        if (typeof v.sog === "number") html += "<br>SOG " + v.sog + " kn";
        if (typeof v.cog === "number") html += " · COG " + Math.round(v.cog) + "°";
        if (v.recorded_at) {
          try {
            html += "<br><small>" + new Date(v.recorded_at).toLocaleString("fr-FR") + "</small>";
          } catch (e) {}
        }
        new window.maplibregl.Marker({ element: marker })
          .setLngLat([v.lon, v.lat])
          .setPopup(new window.maplibregl.Popup({ offset: 18 }).setHTML(html))
          .addTo(map);
      });

      if (!withPos.length) {
        var note = document.createElement("div");
        note.style.cssText = (
          "position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);" +
          "background:rgba(255,255,255,.92);padding:12px 20px;border-radius:6px;" +
          "font-size:14px;color:#6E6E6E;text-align:center;z-index:5;"
        );
        // Texte de fallback configurable via data-no-position-text / data-no-position-detail
        // injectés par le serveur en fonction de la langue détectée.
        var mainText = el.dataset.noPositionText || "Aucune position enregistrée.";
        var detailText = el.dataset.noPositionDetail || (
          "Alimente <code>vessel_positions</code> via le module Tracking " +
          "(POST /api/tracking/upload avec X-API-Token)."
        );
        note.innerHTML = mainText + "<br><small>" + detailText + "</small>";
        el.style.position = "relative";
        el.appendChild(note);
      }
    });
    return true;
  }

  function bindAll() {
    document.querySelectorAll(".js-fleet-map, #fleet-map, #dashboard-map").forEach(function (el) {
      // Si MapLibre n'est pas encore prêt, on retry après load
      if (!initMap(el)) {
        var retry = setInterval(function () {
          if (typeof window.maplibregl !== "undefined") {
            clearInterval(retry);
            initMap(el);
          }
        }, 100);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindAll);
  } else {
    bindAll();
  }
})();
