/*
 * Map-based leg creator.
 *
 * UX:
 * - World map with every active port displayed as a clickable dot.
 * - Click an empty area → snap to closest port within 50 km.
 * - Click a dot → select it directly (no snap).
 * - 1st pick = POL, 2nd = POD, 3rd starts over.
 * - Great-circle line + distance + ETA at 8 kn.
 */
(function () {
  "use strict";

  var state = { pol: null, pod: null, markers: [], routeLayerId: "leg-route" };
  var portsSourceId = "ports-src";
  var portsLayerId = "ports-layer";
  var portsLayerHover = "ports-layer-hover";

  function ready() {
    var container = document.getElementById("leg-map");
    if (!container || typeof maplibregl === "undefined") return;

    var token = container.dataset.maptilerToken;
    // Style compagnie : MapTiler Outdoor (cohérent avec fleet-map.js).
    // Surchargeable via data-map-style sur #leg-map.
    var mapStyle = container.dataset.mapStyle || "outdoor-v2";
    var style = token
      ? "https://api.maptiler.com/maps/" + encodeURIComponent(mapStyle) +
        "/style.json?key=" + encodeURIComponent(token)
      : "https://demotiles.maplibre.org/style.json";

    var map = new maplibregl.Map({
      container: "leg-map",
      style: style,
      center: [-30, 40],
      zoom: 2,
      attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("load", function () {
      // Empty source + circle layer for ports.
      map.addSource(portsSourceId, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: portsLayerId,
        type: "circle",
        source: portsSourceId,
        paint: {
          "circle-radius": [
            "interpolate", ["linear"], ["zoom"],
            1, 2,   // zoom 1 → 2 px
            5, 4,   // zoom 5 → 4 px
            10, 6,
          ],
          "circle-color": "#87BD29",
          "circle-stroke-color": "#0D5966",
          "circle-stroke-width": 1.5,
          "circle-opacity": 0.85,
        },
      });
      // Hover effect
      map.addLayer({
        id: portsLayerHover,
        type: "circle",
        source: portsSourceId,
        filter: ["==", ["get", "id"], -1],
        paint: {
          "circle-radius": 10,
          "circle-color": "#B47148",
          "circle-stroke-color": "#fff",
          "circle-stroke-width": 2,
        },
      });

      loadVisiblePorts(map);
      map.on("moveend", function () { loadVisiblePorts(map); });

      map.on("mouseenter", portsLayerId, function (e) {
        map.getCanvas().style.cursor = "pointer";
        if (e.features && e.features[0]) {
          map.setFilter(portsLayerHover, ["==", ["get", "id"], e.features[0].properties.id]);
        }
      });
      map.on("mouseleave", portsLayerId, function () {
        map.getCanvas().style.cursor = "";
        map.setFilter(portsLayerHover, ["==", ["get", "id"], -1]);
      });

      // Click on a port marker → direct selection (no snap).
      map.on("click", portsLayerId, function (e) {
        if (!e.features || !e.features[0]) return;
        var f = e.features[0];
        var p = {
          id: f.properties.id,
          locode: f.properties.locode,
          name: f.properties.name,
          country: f.properties.country,
          longitude: f.geometry.coordinates[0],
          latitude: f.geometry.coordinates[1],
        };
        pickPort(map, p);
      });
    });

    // Click on an empty zone → snap to closest within 50 km.
    map.on("click", function (e) {
      // Skip if user clicked on a port marker (the layer handler runs first).
      var feats = map.queryRenderedFeatures(e.point, { layers: [portsLayerId] });
      if (feats.length) return;
      fetch("/api/v1/ports/nearby?lat=" + e.lngLat.lat + "&lon=" + e.lngLat.lng + "&radius_km=50&limit=1")
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (rows) {
          if (!rows.length) return;
          pickPort(map, rows[0]);
        });
    });

    document.getElementById("reset-map").addEventListener("click", function () {
      reset(map);
    });
    document.getElementById("swap-points").addEventListener("click", function () {
      if (!state.pol || !state.pod) return;
      var t = state.pol;
      state.pol = state.pod;
      state.pod = t;
      renderAll(map);
    });

    var etd = document.getElementById("etd");
    var defaultEtd = new Date();
    defaultEtd.setDate(defaultEtd.getDate() + 7);
    defaultEtd.setHours(8, 0, 0, 0);
    etd.value = isoLocal(defaultEtd);
    etd.addEventListener("change", updateEta);
  }

  function loadVisiblePorts(map) {
    var b = map.getBounds();
    var url = "/api/v1/ports/bbox?min_lat=" + b.getSouth() +
      "&min_lon=" + b.getWest() +
      "&max_lat=" + b.getNorth() +
      "&max_lon=" + b.getEast() +
      "&limit=2000";
    fetch(url)
      .then(function (r) { return r.ok ? r.json() : { type: "FeatureCollection", features: [] }; })
      .then(function (geojson) {
        var src = map.getSource(portsSourceId);
        if (src) src.setData(geojson);
      })
      .catch(function () { /* keep silent — fallback to manual click snap */ });
  }

  function pickPort(map, p) {
    if (!state.pol) {
      state.pol = p;
    } else if (!state.pod) {
      if (p.id === state.pol.id) return;
      state.pod = p;
    } else {
      state.pol = p;
      state.pod = null;
    }
    renderAll(map);
  }

  function reset(map) {
    state.pol = null;
    state.pod = null;
    state.markers.forEach(function (m) { m.remove(); });
    state.markers = [];
    if (map.getLayer(state.routeLayerId)) map.removeLayer(state.routeLayerId);
    if (map.getSource(state.routeLayerId)) map.removeSource(state.routeLayerId);
    renderInputs();
  }

  function renderAll(map) {
    state.markers.forEach(function (m) { m.remove(); });
    state.markers = [];

    [{ p: state.pol, color: "#0D5966", label: "POL" },
     { p: state.pod, color: "#B47148", label: "POD" }].forEach(function (m) {
      if (!m.p) return;
      var el = document.createElement("div");
      el.style.cssText =
        "width:30px;height:30px;border-radius:50%;background:" + m.color +
        ";color:#fff;display:flex;align-items:center;justify-content:center;" +
        "font-family:'JetBrains Mono', monospace;font-size:11px;font-weight:700;" +
        "border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,0.4);cursor:pointer;";
      el.textContent = m.label;
      var marker = new maplibregl.Marker({ element: el })
        .setLngLat([m.p.longitude, m.p.latitude])
        .setPopup(new maplibregl.Popup({ offset: 18 }).setHTML(
          "<strong>" + escapeHtml(m.p.name) + "</strong><br>" +
          "<span style='font-family:monospace;color:#6E6E6E'>" + escapeHtml(m.p.locode) + "</span>"
        ))
        .addTo(map);
      state.markers.push(marker);
    });

    if (map.getLayer(state.routeLayerId)) map.removeLayer(state.routeLayerId);
    if (map.getSource(state.routeLayerId)) map.removeSource(state.routeLayerId);
    if (state.pol && state.pod) {
      var coords = greatCircle(
        [state.pol.longitude, state.pol.latitude],
        [state.pod.longitude, state.pod.latitude],
        80
      );
      map.addSource(state.routeLayerId, {
        type: "geojson",
        data: { type: "Feature", geometry: { type: "LineString", coordinates: coords } }
      });
      map.addLayer({
        id: state.routeLayerId, type: "line", source: state.routeLayerId,
        paint: { "line-color": "#87BD29", "line-width": 3, "line-dasharray": [2, 1] }
      });
      var bounds = coords.reduce(function (b, c) { return b.extend(c); },
        new maplibregl.LngLatBounds(coords[0], coords[0]));
      map.fitBounds(bounds, { padding: 60, maxZoom: 5, duration: 600 });
    }

    renderInputs();
  }

  function renderInputs() {
    document.getElementById("pol-display").innerHTML = state.pol
      ? "<strong>" + escapeHtml(state.pol.name) + "</strong> <span class='mono text-muted'>(" + escapeHtml(state.pol.locode) + ")</span>"
      : "— cliquer sur la carte —";
    document.getElementById("pod-display").innerHTML = state.pod
      ? "<strong>" + escapeHtml(state.pod.name) + "</strong> <span class='mono text-muted'>(" + escapeHtml(state.pod.locode) + ")</span>"
      : "— cliquer sur la carte —";

    document.getElementById("pol-id").value = state.pol ? state.pol.id : "";
    document.getElementById("pod-id").value = state.pod ? state.pod.id : "";

    var dist = state.pol && state.pod ? haversineNm(state.pol, state.pod) : null;
    document.getElementById("distance-display").textContent = dist
      ? dist.toFixed(0) + " NM (" + (dist * 1.852).toFixed(0) + " km)"
      : "—";

    document.getElementById("submit-btn").disabled = !(state.pol && state.pod);
    updateEta();
  }

  function updateEta() {
    if (!state.pol || !state.pod) return;
    var dist = haversineNm(state.pol, state.pod);
    var hours = dist / 8.0;
    var etdEl = document.getElementById("etd");
    var etaEl = document.getElementById("eta");
    if (!etdEl.value) return;
    var etd = new Date(etdEl.value);
    var eta = new Date(etd.getTime() + hours * 3600 * 1000);
    etaEl.value = isoLocal(eta);
  }

  function haversineNm(a, b) {
    var p1 = a.latitude * Math.PI / 180;
    var p2 = b.latitude * Math.PI / 180;
    var dl = (b.longitude - a.longitude) * Math.PI / 180;
    var x = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * 3440.065 * Math.asin(Math.sqrt(x));
  }

  function greatCircle(from, to, n) {
    var lat1 = from[1] * Math.PI / 180, lon1 = from[0] * Math.PI / 180;
    var lat2 = to[1] * Math.PI / 180, lon2 = to[0] * Math.PI / 180;
    var d = 2 * Math.asin(Math.sqrt(
      Math.sin((lat2 - lat1) / 2) ** 2 +
      Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2
    ));
    if (d === 0) return [from, to];
    var pts = [];
    for (var i = 0; i <= n; i++) {
      var f = i / n;
      var a = Math.sin((1 - f) * d) / Math.sin(d);
      var b = Math.sin(f * d) / Math.sin(d);
      var x = a * Math.cos(lat1) * Math.cos(lon1) + b * Math.cos(lat2) * Math.cos(lon2);
      var y = a * Math.cos(lat1) * Math.sin(lon1) + b * Math.cos(lat2) * Math.sin(lon2);
      var z = a * Math.sin(lat1) + b * Math.sin(lat2);
      pts.push([
        Math.atan2(y, x) * 180 / Math.PI,
        Math.atan2(z, Math.sqrt(x * x + y * y)) * 180 / Math.PI
      ]);
    }
    return pts;
  }

  function isoLocal(d) {
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  }

  function waitMaplibre() {
    if (typeof maplibregl !== "undefined" && document.readyState !== "loading") {
      ready();
    } else {
      setTimeout(waitMaplibre, 80);
    }
  }
  waitMaplibre();
})();
