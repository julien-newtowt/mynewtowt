/*
 * navigation-map.js — carte de la page Performance › Navigation.
 *
 * Conteneur :
 *   <div id="navigation-map" class="js-navigation-map"
 *        data-maptiler-token="..."
 *        data-points='[{"lat":..,"lon":..,"t":"ISO","sog":..,"cog":..}]'
 *        data-dep='{"lat":..,"lon":..,"name":"..","locode":".."}'   (optionnel)
 *        data-arr='{"lat":..,"lon":..,"name":"..","locode":".."}'   (optionnel)
 *        data-weather-url="/navigation/legs/<id>/weather">
 *
 * Dessine :
 *   - le trajet réellement réalisé (LineString teal) + points GPS (cercles verts) ;
 *   - la route théorique orthodromique POL→POD (arc cuivre pointillé) ;
 *   - les marqueurs Départ / Arrivée ;
 *   - les relevés météo historisés (cercles blancs cliquables) chargés en
 *     asynchrone depuis data-weather-url (vent · courant · vague · température).
 *
 * CSP strict : aucun script inline, chargé via <script src defer>.
 */
(function () {
  "use strict";

  function parse(el, key) {
    try { return JSON.parse(el.dataset[key] || "null"); } catch (e) { return null; }
  }

  function compass(deg) {
    if (deg == null) return "";
    var dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
      "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
    return dirs[Math.floor(((deg % 360) + 11.25) / 22.5) % 16];
  }

  /* Arc grand-cercle (slerp cartésien) entre deux [lon,lat]. */
  function greatCircle(from, to, n) {
    function toCart(lat, lng) {
      lat = lat * Math.PI / 180; lng = lng * Math.PI / 180;
      return [Math.cos(lat) * Math.cos(lng), Math.cos(lat) * Math.sin(lng), Math.sin(lat)];
    }
    var a = toCart(from[1], from[0]), b = toCart(to[1], to[0]);
    var dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    dot = Math.max(-1, Math.min(1, dot));
    var omega = Math.acos(dot);
    if (omega === 0) return [from, to];
    var pts = [];
    for (var i = 0; i <= n; i++) {
      var f = i / n;
      var s1 = Math.sin((1 - f) * omega) / Math.sin(omega);
      var s2 = Math.sin(f * omega) / Math.sin(omega);
      var v = [s1 * a[0] + s2 * b[0], s1 * a[1] + s2 * b[1], s1 * a[2] + s2 * b[2]];
      var lat = Math.atan2(v[2], Math.sqrt(v[0] * v[0] + v[1] * v[1])) * 180 / Math.PI;
      var lng = Math.atan2(v[1], v[0]) * 180 / Math.PI;
      pts.push([lng, lat]);
    }
    return pts;
  }

  function endpoint(map, lngLat, color, label, sub) {
    var d = document.createElement("div");
    d.style.cssText = "width:26px;height:26px;border-radius:50%;background:" + color +
      ";color:#fff;display:flex;align-items:center;justify-content:center;font-size:10px;" +
      "font-family:'JetBrains Mono',monospace;font-weight:700;border:3px solid #fff;" +
      "box-shadow:0 2px 8px rgba(0,0,0,.4);cursor:pointer;";
    d.textContent = label;
    new window.maplibregl.Marker({ element: d })
      .setLngLat(lngLat)
      .setPopup(new window.maplibregl.Popup({ offset: 16 }).setHTML(sub))
      .addTo(map);
  }

  function setStatus(text, state) {
    var pill = document.getElementById("weather-status");
    if (!pill) return;
    pill.textContent = text;
    pill.dataset.state = state || "idle";
  }

  function loadWeather(map, url) {
    if (!url) { setStatus("—", "idle"); return; }
    setStatus("Chargement météo…", "loading");
    fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.points || !data.points.length) {
          setStatus("Aucun relevé météo historisé", "empty");
          return;
        }
        var feats = data.points
          .filter(function (p) { return typeof p.lat === "number"; })
          .map(function (p) {
            return {
              type: "Feature",
              geometry: { type: "Point", coordinates: [p.lon, p.lat] },
              properties: p,
            };
          });
        map.addSource("wx-src", {
          type: "geojson",
          data: { type: "FeatureCollection", features: feats },
        });
        map.addLayer({
          id: "wx-layer", type: "circle", source: "wx-src",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 4, 8, 7],
            "circle-color": "#ffffff",
            "circle-stroke-color": "#0D5966",
            "circle-stroke-width": 2.5,
          },
        });
        map.on("click", "wx-layer", function (e) {
          if (!e.features || !e.features[0]) return;
          var p = e.features[0].properties;
          var c = e.features[0].geometry.coordinates;
          var rows = [];
          if (p.t) { try { rows.push("<small>" + new Date(p.t).toLocaleString("fr-FR") + "</small>"); } catch (x) {} }
          if (p.wind_kn != null && p.wind_kn !== "")
            rows.push("Vent : " + compass(+p.wind_dir) + " " + (+p.wind_kn).toFixed(0) + " kn");
          if (p.current_kn != null && p.current_kn !== "")
            rows.push("Courant : " + compass(+p.current_dir) + " " + (+p.current_kn).toFixed(1) + " kn");
          if (p.wave_m != null && p.wave_m !== "")
            rows.push("Houle : " + (+p.wave_m).toFixed(1) + " m" + (p.wave_period_s != null && p.wave_period_s !== "" ? " · " + (+p.wave_period_s).toFixed(0) + " s" : ""));
          if (p.temp_c != null && p.temp_c !== "")
            rows.push("Temp. : " + (+p.temp_c).toFixed(1) + " °C");
          if (rows.length <= 1) rows.push("<em>Données indisponibles</em>");
          new window.maplibregl.Popup({ offset: 12 }).setLngLat(c)
            .setHTML("<strong>Météo au point</strong><br>" + rows.join("<br>")).addTo(map);
        });
        map.on("mouseenter", "wx-layer", function () { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "wx-layer", function () { map.getCanvas().style.cursor = ""; });
        setStatus(data.count + " relevé(s) météo · clic = détail", "ok");
      })
      .catch(function () { setStatus("Erreur de chargement météo", "error"); });
  }

  function initMap(el) {
    if (!el || el.dataset.navBound === "1") return true;
    if (typeof window.maplibregl === "undefined") return false;
    el.dataset.navBound = "1";

    var token = el.dataset.maptilerToken || "";
    var points = (parse(el, "points") || []).filter(function (p) {
      return typeof p.lat === "number" && typeof p.lon === "number";
    });
    var dep = parse(el, "dep");
    var arr = parse(el, "arr");

    var mapStyle = el.dataset.mapStyle || "outdoor-v2";
    var style = token
      ? "https://api.maptiler.com/maps/" + encodeURIComponent(mapStyle) +
        "/style.json?key=" + encodeURIComponent(token)
      : "https://demotiles.maplibre.org/style.json";

    var center = points.length ? [points[0].lon, points[0].lat]
      : (dep ? [dep.lon, dep.lat] : [-30, 40]);
    var map = new window.maplibregl.Map({
      container: el, style: style, center: center,
      zoom: points.length || dep ? 4 : 2,
      attributionControl: { compact: true },
    });
    map.addControl(new window.maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("load", function () {
      var allCoords = [];

      // Route théorique POL→POD (arc cuivre pointillé).
      if (dep && arr) {
        var theo = greatCircle([dep.lon, dep.lat], [arr.lon, arr.lat], 80);
        map.addSource("theo-src", {
          type: "geojson",
          data: { type: "Feature", geometry: { type: "LineString", coordinates: theo } },
        });
        map.addLayer({
          id: "theo-layer", type: "line", source: "theo-src",
          paint: { "line-color": "#B47148", "line-width": 2.5, "line-dasharray": [2, 2], "line-opacity": 0.9 },
        });
        allCoords = allCoords.concat(theo);
      }

      // Trajet réel + points GPS.
      if (points.length) {
        var coords = points.map(function (p) { return [p.lon, p.lat]; });
        map.addSource("real-src", {
          type: "geojson",
          data: { type: "Feature", geometry: { type: "LineString", coordinates: coords } },
        });
        map.addLayer({
          id: "real-layer", type: "line", source: "real-src",
          layout: { "line-join": "round", "line-cap": "round" },
          paint: { "line-color": "#0D5966", "line-width": 3, "line-opacity": 0.9 },
        });
        map.addSource("gps-src", {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: points.map(function (p, i) {
              return {
                type: "Feature",
                geometry: { type: "Point", coordinates: [p.lon, p.lat] },
                properties: { idx: i, t: p.t || "", sog: p.sog == null ? "" : p.sog },
              };
            }),
          },
        });
        map.addLayer({
          id: "gps-layer", type: "circle", source: "gps-src",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 2, 8, 4.5],
            "circle-color": "#87BD29", "circle-stroke-color": "#0D5966", "circle-stroke-width": 1,
            "circle-opacity": 0.85,
          },
        });
        map.on("click", "gps-layer", function (e) {
          if (!e.features || !e.features[0]) return;
          var pr = e.features[0].properties, c = e.features[0].geometry.coordinates;
          var html = "<strong>Point GPS " + (parseInt(pr.idx, 10) + 1) + "/" + points.length + "</strong>";
          if (pr.t) { try { html += "<br><small>" + new Date(pr.t).toLocaleString("fr-FR") + "</small>"; } catch (x) {} }
          if (pr.sog !== "") html += "<br>SOG " + pr.sog + " kn";
          html += "<br><span style='font-family:monospace;color:#6E6E6E'>" + c[1].toFixed(4) + ", " + c[0].toFixed(4) + "</span>";
          new window.maplibregl.Popup({ offset: 10 }).setLngLat(c).setHTML(html).addTo(map);
        });
        map.on("mouseenter", "gps-layer", function () { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "gps-layer", function () { map.getCanvas().style.cursor = ""; });
        allCoords = allCoords.concat(coords);
      }

      if (dep) endpoint(map, [dep.lon, dep.lat], "#0D5966", "POL",
        "<strong>" + (dep.name || "Départ") + "</strong><br><span style='font-family:monospace'>" + (dep.locode || "") + "</span>");
      if (arr) endpoint(map, [arr.lon, arr.lat], "#B47148", "POD",
        "<strong>" + (arr.name || "Arrivée") + "</strong><br><span style='font-family:monospace'>" + (arr.locode || "") + "</span>");

      if (allCoords.length) {
        var bounds = allCoords.reduce(function (b, c) { return b.extend(c); },
          new window.maplibregl.LngLatBounds(allCoords[0], allCoords[0]));
        map.fitBounds(bounds, { padding: 70, maxZoom: 8, duration: 500 });
      } else {
        showEmptyNote(el);
      }

      loadWeather(map, el.dataset.weatherUrl);
    });

    return true;
  }

  function showEmptyNote(el) {
    var note = document.createElement("div");
    note.style.cssText = "position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);" +
      "background:rgba(255,255,255,.92);padding:12px 20px;border-radius:6px;font-size:14px;" +
      "color:#6E6E6E;text-align:center;z-index:5;";
    note.innerHTML = (el.dataset.noPositionText || "Aucun point GPS.") +
      (el.dataset.noPositionDetail ? "<br><small>" + el.dataset.noPositionDetail + "</small>" : "");
    el.style.position = "relative";
    el.appendChild(note);
  }

  function bindAll() {
    document.querySelectorAll(".js-navigation-map").forEach(function (el) {
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
