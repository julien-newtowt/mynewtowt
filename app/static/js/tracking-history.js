/*
 * tracking-history.js — trace le parcours réellement réalisé sur /tracking.
 *
 * Conteneur attendu :
 *   <div id="tracking-history-map" class="js-tracking-history"
 *        data-maptiler-token="..."
 *        data-points='[{"lat":..,"lon":..,"t":"ISO","sog":..,"cog":..}, ...]'>
 *
 * Dessine :
 *   - une LineString reliant tous les points dans l'ordre chronologique
 *     (le trajet réellement parcouru) ;
 *   - un petit cercle par point (popup date/SOG/COG au clic) ;
 *   - des marqueurs Départ (teal) / Dernier point (cuivre).
 *
 * CSP strict : aucun script inline, chargé via <script src defer>.
 */
(function () {
  "use strict";

  var SRC_LINE = "track-line-src";
  var SRC_PTS = "track-pts-src";

  function initMap(el) {
    if (!el || el.dataset.trackBound === "1") return true;
    if (typeof window.maplibregl === "undefined") return false;
    el.dataset.trackBound = "1";

    var token = el.dataset.maptilerToken || "";
    var points = [];
    try { points = JSON.parse(el.dataset.points || "[]"); } catch (e) { points = []; }
    points = points.filter(function (p) {
      return typeof p.lat === "number" && typeof p.lon === "number";
    });

    var mapStyle = el.dataset.mapStyle || "outdoor-v2";
    var style = token
      ? "https://api.maptiler.com/maps/" + encodeURIComponent(mapStyle) +
        "/style.json?key=" + encodeURIComponent(token)
      : "https://demotiles.maplibre.org/style.json";

    var center = points.length ? [points[0].lon, points[0].lat] : [-30, 40];
    var map = new window.maplibregl.Map({
      container: el,
      style: style,
      center: center,
      zoom: points.length ? 4 : 2,
      attributionControl: { compact: true },
    });
    map.addControl(new window.maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("load", function () {
      if (!points.length) {
        showEmptyNote(el);
        return;
      }

      var coords = points.map(function (p) { return [p.lon, p.lat]; });

      // Trait reliant tous les points (parcours réalisé).
      map.addSource(SRC_LINE, {
        type: "geojson",
        data: { type: "Feature", geometry: { type: "LineString", coordinates: coords } },
      });
      map.addLayer({
        id: SRC_LINE,
        type: "line",
        source: SRC_LINE,
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#0D5966", "line-width": 3, "line-opacity": 0.85 },
      });

      // Points enregistrés (cercles cliquables).
      map.addSource(SRC_PTS, {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: points.map(function (p, i) {
            return {
              type: "Feature",
              geometry: { type: "Point", coordinates: [p.lon, p.lat] },
              properties: {
                idx: i, t: p.t || "",
                sog: p.sog == null ? "" : p.sog,
                cog: p.cog == null ? "" : p.cog,
              },
            };
          }),
        },
      });
      map.addLayer({
        id: SRC_PTS,
        type: "circle",
        source: SRC_PTS,
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 2.5, 6, 4, 10, 6],
          "circle-color": "#87BD29",
          "circle-stroke-color": "#0D5966",
          "circle-stroke-width": 1.2,
          "circle-opacity": 0.9,
        },
      });

      map.on("click", SRC_PTS, function (e) {
        if (!e.features || !e.features[0]) return;
        var pr = e.features[0].properties;
        var c = e.features[0].geometry.coordinates;
        var html = "<strong>Point " + (parseInt(pr.idx, 10) + 1) + "/" + points.length + "</strong>";
        if (pr.t) {
          try { html += "<br><small>" + new Date(pr.t).toLocaleString("fr-FR") + "</small>"; }
          catch (err) { html += "<br><small>" + pr.t + "</small>"; }
        }
        if (pr.sog !== "") html += "<br>SOG " + pr.sog + " kn";
        if (pr.cog !== "") html += " · COG " + Math.round(pr.cog) + "°";
        html += "<br><span style='font-family:monospace;color:#6E6E6E'>" +
          c[1].toFixed(4) + ", " + c[0].toFixed(4) + "</span>";
        new window.maplibregl.Popup({ offset: 12 }).setLngLat(c).setHTML(html).addTo(map);
      });
      map.on("mouseenter", SRC_PTS, function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", SRC_PTS, function () { map.getCanvas().style.cursor = ""; });

      // Marqueurs Départ / Dernier point.
      addEndpoint(map, coords[0], "#0D5966", "Départ", points[0].t);
      if (coords.length > 1) {
        addEndpoint(map, coords[coords.length - 1], "#B47148", "Dernier point",
          points[points.length - 1].t);
      }

      // Cadrage sur l'emprise du trajet.
      var bounds = coords.reduce(function (b, c) { return b.extend(c); },
        new window.maplibregl.LngLatBounds(coords[0], coords[0]));
      map.fitBounds(bounds, { padding: 60, maxZoom: 8, duration: 500 });
    });

    return true;
  }

  function addEndpoint(map, lngLat, color, label, t) {
    var el = document.createElement("div");
    el.style.cssText =
      "width:16px;height:16px;border-radius:50%;background:" + color +
      ";border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.4);cursor:pointer;";
    var html = "<strong>" + label + "</strong>";
    if (t) {
      try { html += "<br><small>" + new Date(t).toLocaleString("fr-FR") + "</small>"; }
      catch (e) { /* ignore */ }
    }
    new window.maplibregl.Marker({ element: el })
      .setLngLat(lngLat)
      .setPopup(new window.maplibregl.Popup({ offset: 14 }).setHTML(html))
      .addTo(map);
  }

  function showEmptyNote(el) {
    var note = document.createElement("div");
    note.style.cssText =
      "position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);" +
      "background:rgba(255,255,255,.92);padding:12px 20px;border-radius:6px;" +
      "font-size:14px;color:#6E6E6E;text-align:center;z-index:5;";
    var main = el.dataset.noPositionText || "Aucun point enregistré.";
    var detail = el.dataset.noPositionDetail || "";
    note.innerHTML = main + (detail ? "<br><small>" + detail + "</small>" : "");
    el.style.position = "relative";
    el.appendChild(note);
  }

  function bindAll() {
    document.querySelectorAll(".js-tracking-history").forEach(function (el) {
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
