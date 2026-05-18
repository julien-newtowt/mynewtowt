/*
 * Map-based leg creator.
 *
 * Workflow:
 * - User clicks the map → snap to closest port within 50 km (POL on first
 *   click, POD on second).
 * - Draws a great-circle line between the two ports.
 * - Computes orthodromic distance (NM) and a default ETA = ETD + dist/8 kn.
 * - Submits the standard /planning/legs/new endpoint with hidden port IDs.
 */
(function () {
  "use strict";

  var state = { pol: null, pod: null, markers: [], routeLayerId: "leg-route" };

  function ready() {
    var container = document.getElementById("leg-map");
    if (!container || typeof maplibregl === "undefined") return;

    var token = container.dataset.maptilerToken;
    var style;
    if (token) {
      style = "https://api.maptiler.com/maps/streets-v2/style.json?key=" + encodeURIComponent(token);
    } else {
      // Fallback : OSM raster style hosted by demotiles.maplibre.org
      style = "https://demotiles.maplibre.org/style.json";
    }

    var map = new maplibregl.Map({
      container: "leg-map",
      style: style,
      center: [-30, 40],   // mid-Atlantic
      zoom: 2,
      attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("click", function (e) {
      handleClick(map, e.lngLat.lat, e.lngLat.lng);
    });

    document.getElementById("reset-map").addEventListener("click", function () {
      reset(map);
    });
    document.getElementById("swap-points").addEventListener("click", function () {
      if (!state.pol || !state.pod) return;
      var tmp = state.pol;
      state.pol = state.pod;
      state.pod = tmp;
      renderAll(map);
    });

    // Default ETD = now + 7 days, ETA computed when both ports set.
    var etd = document.getElementById("etd");
    var defaultEtd = new Date();
    defaultEtd.setDate(defaultEtd.getDate() + 7);
    defaultEtd.setHours(8, 0, 0, 0);
    etd.value = isoLocal(defaultEtd);
    etd.addEventListener("change", function () { updateEta(); });
  }

  function handleClick(map, lat, lng) {
    fetch("/api/v1/ports/nearby?lat=" + lat + "&lon=" + lng + "&radius_km=50&limit=1")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (rows) {
        if (!rows.length) {
          alert("Aucun port connu dans un rayon de 50 km.");
          return;
        }
        var port = rows[0];
        if (!state.pol) {
          state.pol = port;
        } else if (!state.pod) {
          if (port.id === state.pol.id) {
            alert("Le port d'arrivée doit être différent du départ.");
            return;
          }
          state.pod = port;
        } else {
          // Both set → start over with this as new POL
          state.pol = port;
          state.pod = null;
        }
        renderAll(map);
      })
      .catch(function (err) {
        console.error(err);
        alert("Erreur de chargement des ports proches.");
      });
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
    // Markers
    state.markers.forEach(function (m) { m.remove(); });
    state.markers = [];

    [{ p: state.pol, color: "#0D5966", label: "POL" },
     { p: state.pod, color: "#B47148", label: "POD" }].forEach(function (m) {
      if (!m.p) return;
      var el = document.createElement("div");
      el.style.cssText =
        "width:28px;height:28px;border-radius:50%;background:" + m.color +
        ";color:#fff;display:flex;align-items:center;justify-content:center;" +
        "font-family:'JetBrains Mono', monospace;font-size:11px;font-weight:700;" +
        "border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.3);";
      el.textContent = m.label;
      var marker = new maplibregl.Marker({ element: el })
        .setLngLat([m.p.longitude, m.p.latitude])
        .setPopup(
          new maplibregl.Popup({ offset: 18 })
            .setHTML(
              "<strong>" + escapeHtml(m.p.name) + "</strong><br>" +
              "<span style='font-family:monospace;color:#6E6E6E'>" + escapeHtml(m.p.locode) + "</span>"
            )
        )
        .addTo(map);
      state.markers.push(marker);
    });

    // Great-circle route between POL and POD
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
        id: state.routeLayerId,
        type: "line",
        source: state.routeLayerId,
        paint: { "line-color": "#87BD29", "line-width": 3, "line-dasharray": [2, 1] }
      });
      // Fit bounds to the route
      var bounds = coords.reduce(
        function (b, c) { return b.extend(c); },
        new maplibregl.LngLatBounds(coords[0], coords[0])
      );
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
    var hours = dist / 8.0;  // 8 kn average
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
    // Returns n+1 [lng, lat] points along the great-circle path.
    var lat1 = from[1] * Math.PI / 180, lon1 = from[0] * Math.PI / 180;
    var lat2 = to[1] * Math.PI / 180, lon2 = to[0] * Math.PI / 180;
    var d = 2 * Math.asin(Math.sqrt(
      Math.sin((lat2 - lat1) / 2) ** 2 +
      Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2
    ));
    if (d === 0) return [from, to];
    var points = [];
    for (var i = 0; i <= n; i++) {
      var f = i / n;
      var a = Math.sin((1 - f) * d) / Math.sin(d);
      var b = Math.sin(f * d) / Math.sin(d);
      var x = a * Math.cos(lat1) * Math.cos(lon1) + b * Math.cos(lat2) * Math.cos(lon2);
      var y = a * Math.cos(lat1) * Math.sin(lon1) + b * Math.cos(lat2) * Math.sin(lon2);
      var z = a * Math.sin(lat1) + b * Math.sin(lat2);
      points.push([
        Math.atan2(y, x) * 180 / Math.PI,
        Math.atan2(z, Math.sqrt(x * x + y * y)) * 180 / Math.PI
      ]);
    }
    return points;
  }

  function isoLocal(d) {
    // YYYY-MM-DDTHH:MM in local time, suitable for <input type="datetime-local">.
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  // MapLibre loads async — wait for the script tag + DOM ready.
  function waitMaplibre() {
    if (typeof maplibregl !== "undefined" && document.readyState !== "loading") {
      ready();
    } else {
      setTimeout(waitMaplibre, 80);
    }
  }
  waitMaplibre();
})();
