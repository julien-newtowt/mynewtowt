/*
 * route-map.js — MapLibre GL map for the route detail page.
 *
 * Draws a great-circle arc from POL to POD using slerp in Cartesian space,
 * with a teal marker at POL and a copper marker at POD.
 *
 * Expected HTML container:
 *   <div class="js-route-map"
 *        data-maptiler-token="..."
 *        data-pol-lat="48.3904" data-pol-lng="-4.4861"
 *        data-pod-lat="14.6928" data-pod-lng="-17.4467"
 *        data-pol-name="Brest"
 *        data-pod-name="Dakar">
 *   </div>
 *
 * The container must have a CSS height set on it (e.g. height: 400px).
 * Compatible with CSP strict (no inline scripts, no eval).
 * Loaded via <script src="…" defer> in the page.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /* Great-circle interpolation using slerp in Cartesian space            */
  /* ------------------------------------------------------------------ */

  /**
   * Convert geographic coordinates (degrees) to a unit Cartesian vector.
   * @param {number} latDeg
   * @param {number} lngDeg
   * @returns {number[]} [x, y, z]
   */
  function toCartesian(latDeg, lngDeg) {
    var lat = latDeg * Math.PI / 180;
    var lng = lngDeg * Math.PI / 180;
    return [
      Math.cos(lat) * Math.cos(lng),
      Math.cos(lat) * Math.sin(lng),
      Math.sin(lat)
    ];
  }

  /**
   * Convert a unit Cartesian vector back to geographic coordinates (degrees).
   * @param {number[]} v  [x, y, z]
   * @returns {number[]} [lngDeg, latDeg]  (GeoJSON order: lon first)
   */
  function fromCartesian(v) {
    var lat = Math.atan2(v[2], Math.sqrt(v[0] * v[0] + v[1] * v[1])) * 180 / Math.PI;
    var lng = Math.atan2(v[1], v[0]) * 180 / Math.PI;
    return [lng, lat];
  }

  /**
   * Normalise a 3-vector to unit length.
   * @param {number[]} v
   * @returns {number[]}
   */
  function normalise(v) {
    var len = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    if (len === 0) return v;
    return [v[0] / len, v[1] / len, v[2] / len];
  }

  /**
   * Produce n+1 points along the great-circle arc from (lat1,lng1) to (lat2,lng2)
   * using spherical linear interpolation (slerp) in Cartesian space.
   *
   * @param {number} lat1  Departure latitude  (degrees)
   * @param {number} lng1  Departure longitude (degrees)
   * @param {number} lat2  Arrival latitude    (degrees)
   * @param {number} lng2  Arrival longitude   (degrees)
   * @param {number} n     Number of intermediate segments (total n+1 points)
   * @returns {number[][]} Array of [lng, lat] pairs (GeoJSON order)
   */
  function greatCirclePoints(lat1, lng1, lat2, lng2, n) {
    var a = toCartesian(lat1, lng1);
    var b = toCartesian(lat2, lng2);

    /* Angular distance between the two vectors. */
    var dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    /* Clamp to [-1, 1] to guard against floating-point overshoot. */
    dot = Math.max(-1, Math.min(1, dot));
    var omega = Math.acos(dot);

    var pts = [];

    if (omega < 1e-10) {
      /* Points are coincident — return a two-point degenerate line. */
      return [fromCartesian(a), fromCartesian(b)];
    }

    for (var i = 0; i <= n; i++) {
      var t = i / n;
      /* Slerp formula: slerp(a, b, t) = sin((1-t)*ω)/sin(ω)*a + sin(t*ω)/sin(ω)*b */
      var scaleA = Math.sin((1 - t) * omega) / Math.sin(omega);
      var scaleB = Math.sin(t * omega) / Math.sin(omega);
      var v = normalise([
        scaleA * a[0] + scaleB * b[0],
        scaleA * a[1] + scaleB * b[1],
        scaleA * a[2] + scaleB * b[2]
      ]);
      pts.push(fromCartesian(v));
    }

    return pts;
  }

  /* ------------------------------------------------------------------ */
  /* HTML escaping                                                         */
  /* ------------------------------------------------------------------ */

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  /* ------------------------------------------------------------------ */
  /* Map initialisation                                                    */
  /* ------------------------------------------------------------------ */

  function initMap(el) {
    if (!el || el.dataset.routeMapBound === "1") return;
    if (typeof window.maplibregl === "undefined") return false;
    el.dataset.routeMapBound = "1";

    /* Read coordinates from data attributes. */
    var polLat = parseFloat(el.dataset.polLat);
    var polLng = parseFloat(el.dataset.polLng);
    var podLat = parseFloat(el.dataset.podLat);
    var podLng = parseFloat(el.dataset.podLng);
    var polName = el.dataset.polName || "POL";
    var podName = el.dataset.podName || "POD";
    var token = el.dataset.maptilerToken || "";

    /* Guard — if coordinates are missing, display a fallback message. */
    if (isNaN(polLat) || isNaN(polLng) || isNaN(podLat) || isNaN(podLng)) {
      var msg = document.createElement("p");
      msg.className = "text-muted text-center p-4";
      msg.textContent = "Coordonnées manquantes — carte indisponible.";
      el.appendChild(msg);
      return true;
    }

    /* Use the ocean style for a maritime look. Fall back to demo tiles if
       no MapTiler token is provided (e.g. in development). */
    var style = token
      ? "https://api.maptiler.com/maps/ocean/style.json?key=" + encodeURIComponent(token)
      : "https://demotiles.maplibre.org/style.json";

    /* Compute an initial centre midpoint for the starting view. */
    var centerLat = (polLat + podLat) / 2;
    var centerLng = (polLng + podLng) / 2;

    var map = new window.maplibregl.Map({
      container: el,
      style: style,
      center: [centerLng, centerLat],
      zoom: 3,
      pitchWithRotate: false,
      attributionControl: { compact: true }
    });
    map.addControl(new window.maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("load", function () {

      /* ---- Great-circle route line ---- */

      /* 60 intermediate segments → 61 points for a smooth arc. */
      var routeCoords = greatCirclePoints(polLat, polLng, podLat, podLng, 60);

      map.addSource("route-line", {
        type: "geojson",
        data: {
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: routeCoords
          }
        }
      });

      map.addLayer({
        id: "route-line",
        type: "line",
        source: "route-line",
        layout: {
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": "#0D5966",  /* teal NEWTOWT */
          "line-width": 3,
          "line-opacity": 0.9
        }
      });

      /* ---- POL marker (teal) ---- */

      var polEl = document.createElement("div");
      polEl.className = "route-map-marker route-map-marker--pol";
      polEl.setAttribute("aria-label", "Départ : " + polName);

      new window.maplibregl.Marker({ element: polEl, anchor: "center" })
        .setLngLat([polLng, polLat])
        .setPopup(
          new window.maplibregl.Popup({ offset: 18 })
            .setHTML("<strong>" + escapeHtml(polName) + "</strong>")
        )
        .addTo(map);

      /* ---- POD marker (copper) ---- */

      var podEl = document.createElement("div");
      podEl.className = "route-map-marker route-map-marker--pod";
      podEl.setAttribute("aria-label", "Arrivée : " + podName);

      new window.maplibregl.Marker({ element: podEl, anchor: "center" })
        .setLngLat([podLng, podLat])
        .setPopup(
          new window.maplibregl.Popup({ offset: 18 })
            .setHTML("<strong>" + escapeHtml(podName) + "</strong>")
        )
        .addTo(map);

      /* ---- Fit bounds to show both ports ---- */

      var bounds = new window.maplibregl.LngLatBounds(
        [polLng, polLat],
        [polLng, polLat]
      );
      bounds.extend([podLng, podLat]);
      /* Extend bounds to the full arc so the line is fully visible. */
      routeCoords.forEach(function (coord) { bounds.extend(coord); });

      map.fitBounds(bounds, {
        padding: { top: 60, bottom: 60, left: 60, right: 60 },
        maxZoom: 8,
        duration: 600
      });
    });

    return true;
  }

  /* ------------------------------------------------------------------ */
  /* Bootstrap — wait for MapLibre and DOM to be ready                    */
  /* ------------------------------------------------------------------ */

  function bindAll() {
    document.querySelectorAll(".js-route-map").forEach(function (el) {
      /* If MapLibre is not yet available, poll until it is. */
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
