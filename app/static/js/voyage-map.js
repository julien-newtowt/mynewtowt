/*
 * Voyage map — trace réelle d'une traversée sur la page publique /voyage/{ref}.
 *
 * Conteneur attendu :
 *   <div class="js-voyage-map"
 *        data-maptiler-token="..."
 *        data-track='[[lon,lat],[lon,lat],...]'
 *        data-ports='[{"name":"Fécamp","lat":49.76,"lon":0.37,"kind":"pol"}]'
 *        data-vessel-name="Anemos"></div>
 *
 * Dessine la polyligne de la trace GPS (teal), les marqueurs POL/POD (sable /
 * cuivre) et la dernière position connue (vert). Compatible CSP strict —
 * aucun script inline, chargé via <script src="…" defer>.
 */
(function () {
  "use strict";

  var COLORS = { track: "#0D5966", pol: "#B47148", pod: "#87BD29", last: "#87BD29" };

  function portMarker(map, port) {
    var el = document.createElement("div");
    el.style.cssText =
      "width:14px;height:14px;border-radius:50%;background:" +
      (port.kind === "pol" ? COLORS.pol : COLORS.pod) +
      ";border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35);";
    el.title = port.name || "";
    new window.maplibregl.Marker({ element: el })
      .setLngLat([port.lon, port.lat])
      .setPopup(new window.maplibregl.Popup({ offset: 12 }).setText(port.name || ""))
      .addTo(map);
  }

  function initMap(el) {
    if (!el || el.dataset.voyageMapBound === "1") return true;
    if (typeof window.maplibregl === "undefined") return false;
    el.dataset.voyageMapBound = "1";

    var token = el.dataset.maptilerToken || "";
    var track = [];
    var ports = [];
    try { track = JSON.parse(el.dataset.track || "[]"); } catch (e) { track = []; }
    try { ports = JSON.parse(el.dataset.ports || "[]"); } catch (e) { ports = []; }

    var style = token
      ? "https://api.maptiler.com/maps/outdoor-v2/style.json?key=" + encodeURIComponent(token)
      : "https://demotiles.maplibre.org/style.json";

    var map = new window.maplibregl.Map({
      container: el,
      style: style,
      center: [-30, 30],
      zoom: 2,
      attributionControl: { compact: true },
    });
    map.addControl(new window.maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("load", function () {
      if (track.length > 1) {
        map.addSource("voyage-track", {
          type: "geojson",
          data: { type: "Feature", geometry: { type: "LineString", coordinates: track } },
        });
        map.addLayer({
          id: "voyage-track-line",
          type: "line",
          source: "voyage-track",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": COLORS.track, "line-width": 3, "line-opacity": 0.9 },
        });
      }

      ports.forEach(function (p) {
        if (typeof p.lat === "number" && typeof p.lon === "number") portMarker(map, p);
      });

      if (track.length) {
        var lastPt = track[track.length - 1];
        var el2 = document.createElement("div");
        el2.style.cssText =
          "width:18px;height:18px;border-radius:50%;background:" + COLORS.last +
          ";border:3px solid #fff;box-shadow:0 1px 5px rgba(0,0,0,.4);";
        el2.title = el.dataset.vesselName || "";
        new window.maplibregl.Marker({ element: el2 })
          .setLngLat(lastPt)
          .setPopup(
            new window.maplibregl.Popup({ offset: 14 }).setText(el.dataset.vesselName || "")
          )
          .addTo(map);
      }

      // Cadre la carte sur l'ensemble trace + ports.
      var all = track.slice();
      ports.forEach(function (p) {
        if (typeof p.lat === "number" && typeof p.lon === "number") all.push([p.lon, p.lat]);
      });
      if (all.length > 1) {
        var b = new window.maplibregl.LngLatBounds(all[0], all[0]);
        all.forEach(function (c) { b.extend(c); });
        map.fitBounds(b, { padding: 50, maxZoom: 6, duration: 400 });
      } else if (all.length === 1) {
        map.jumpTo({ center: all[0], zoom: 4 });
      }
    });
    return true;
  }

  function bindAll() {
    document.querySelectorAll(".js-voyage-map").forEach(function (el) {
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
