/*
 * dashenv-voyage-map.js — carte MapLibre d'un voyage (dashboard environnemental,
 * page 2 « Suivi opérationnel »), route coloriée par catégorie de propulsion.
 *
 * Conteneur :
 *   <div id="dashenv-voyage-map" class="js-dashenv-voyage-map"
 *        data-maptiler-token="..."
 *        data-points='[{ "lat":.., "lon":.., "type":"noon", "label":".." }]'
 *        data-segments='[{ "from":[lon,lat], "to":[lon,lat], "color":"#87BD29",
 *                          "category":"velique_pur" }]'>
 *
 * Chaque segment (entre deux positions d'événements consécutives) est tracé
 * dans la couleur de sa catégorie de propulsion dominante (tranche 4 h, spec
 * §5.4) — calcul serveur, le JS n'affiche que ce qui est fourni. Points GPS =
 * cercles teal. Pattern répliqué de navigation-map.js (init MapLibre, token
 * MapTiler, CSP-safe : script externe, aucun inline).
 */
(function () {
  "use strict";

  function parse(el, key) {
    try { return JSON.parse(el.dataset[key] || "null"); } catch (e) { return null; }
  }

  var CAT_LABELS = {
    velique_pur: "Vélique pur",
    hybride: "Hybride",
    mecanique: "Mécanique pur",
    statique: "Statique / dérive",
  };

  function initMap(el) {
    if (!el || el.dataset.dashenvBound === "1") return true;
    if (typeof window.maplibregl === "undefined") return false;
    el.dataset.dashenvBound = "1";

    var token = el.dataset.maptilerToken || "";
    var points = parse(el, "points") || [];
    var segments = parse(el, "segments") || [];

    if (!points.length) { showEmptyNote(el); return true; }

    var style = token
      ? "https://api.maptiler.com/maps/outdoor-v2/style.json?key=" + encodeURIComponent(token)
      : "https://demotiles.maplibre.org/style.json";

    var map = new window.maplibregl.Map({
      container: el, style: style, center: [-30, 40], zoom: 2,
      attributionControl: { compact: true },
    });
    map.addControl(new window.maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("load", function () {
      var bounds = null;
      function extend(c) {
        if (!c || typeof c[0] !== "number") return;
        if (!bounds) bounds = new window.maplibregl.LngLatBounds(c, c);
        else bounds.extend(c);
      }

      // Segments coloriés par catégorie de propulsion.
      segments.forEach(function (seg, i) {
        if (!seg.from || !seg.to) return;
        var sid = "seg" + i;
        map.addSource(sid, { type: "geojson", data: {
          type: "Feature",
          properties: { category: seg.category || "" },
          geometry: { type: "LineString", coordinates: [seg.from, seg.to] },
        } });
        map.addLayer({ id: sid, type: "line", source: sid,
          layout: { "line-join": "round", "line-cap": "round" },
          paint: { "line-color": seg.color || "#0D5966", "line-width": 4, "line-opacity": 0.9 } });
        map.on("click", sid, function (e) {
          var c = e.lngLat;
          new window.maplibregl.Popup({ offset: 8 }).setLngLat(c)
            .setHTML("<strong>" + (CAT_LABELS[seg.category] || "Segment") + "</strong>").addTo(map);
        });
        extend(seg.from); extend(seg.to);
      });

      // Points GPS (positions d'événements).
      var feats = points
        .filter(function (p) { return typeof p.lat === "number" && typeof p.lon === "number"; })
        .map(function (p) {
          return { type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
            properties: { type: p.type || "", label: p.label || "" } };
        });
      if (feats.length) {
        map.addSource("evpts", { type: "geojson", data: { type: "FeatureCollection", features: feats } });
        map.addLayer({ id: "evpts", type: "circle", source: "evpts",
          paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 3, 8, 5],
            "circle-color": "#0D5966", "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
        map.on("click", "evpts", function (e) {
          if (!e.features || !e.features[0]) return;
          var pr = e.features[0].properties, c = e.features[0].geometry.coordinates;
          new window.maplibregl.Popup({ offset: 8 }).setLngLat(c)
            .setHTML("<strong>" + (pr.label || pr.type) + "</strong><br><span style='font-family:monospace;color:#6E6E6E'>"
              + c[1].toFixed(3) + ", " + c[0].toFixed(3) + "</span>").addTo(map);
        });
        map.on("mouseenter", "evpts", function () { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "evpts", function () { map.getCanvas().style.cursor = ""; });
        feats.forEach(function (f) { extend(f.geometry.coordinates); });
      }

      if (bounds) map.fitBounds(bounds, { padding: 50, maxZoom: 8, duration: 400 });
    });
    return true;
  }

  function showEmptyNote(el) {
    var note = document.createElement("div");
    note.style.cssText = "position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);" +
      "background:rgba(255,255,255,.92);padding:12px 20px;border-radius:6px;font-size:14px;" +
      "color:#6E6E6E;text-align:center;z-index:5;";
    note.textContent = el.dataset.noPositionText || "Aucune position pour ce voyage.";
    el.style.position = "relative";
    el.appendChild(note);
  }

  function bindAll() {
    document.querySelectorAll(".js-dashenv-voyage-map").forEach(function (el) {
      if (!initMap(el)) {
        var retry = setInterval(function () {
          if (typeof window.maplibregl !== "undefined") { clearInterval(retry); initMap(el); }
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
