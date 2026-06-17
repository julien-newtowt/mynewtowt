/*
 * navigation-map.js — carte multi-legs de la page Performance › Navigation.
 *
 * Conteneur :
 *   <div id="navigation-map" class="js-navigation-map"
 *        data-maptiler-token="..."
 *        data-legs='[{ "leg_code":"1CFRBR6", "color":"#0D5966",
 *                      "points":[{lat,lon,t,sog,cog}],
 *                      "weather":[{lat,lon,t,wind_kn,...}],
 *                      "dep":{lat,lon,name,locode}, "arr":{...} }, ...]'>
 *
 * Pour chaque leg : trajet réel (LineString couleur du leg), points GPS,
 * route théorique POL→POD (arc pointillé), marqueurs POL/POD, et relevés météo
 * historisés (cercles blancs cliquables). Zoom automatique sur l'ensemble des
 * points au chargement.
 *
 * CSP strict : aucun script inline, chargé via <script src defer>.
 */
(function () {
  "use strict";

  function parse(el, key) {
    try { return JSON.parse(el.dataset[key] || "null"); } catch (e) { return null; }
  }

  function compass(deg) {
    if (deg == null || deg === "") return "";
    var dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
      "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];
    return dirs[Math.floor(((deg % 360) + 11.25) / 22.5) % 16];
  }

  function greatCircle(from, to, n) {
    function toCart(lat, lng) {
      lat = lat * Math.PI / 180; lng = lng * Math.PI / 180;
      return [Math.cos(lat) * Math.cos(lng), Math.cos(lat) * Math.sin(lng), Math.sin(lat)];
    }
    var a = toCart(from[1], from[0]), b = toCart(to[1], to[0]);
    var dot = Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]));
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
    d.style.cssText = "width:24px;height:24px;border-radius:50%;background:" + color +
      ";color:#fff;display:flex;align-items:center;justify-content:center;font-size:9px;" +
      "font-family:'JetBrains Mono',monospace;font-weight:700;border:3px solid #fff;" +
      "box-shadow:0 2px 8px rgba(0,0,0,.4);cursor:pointer;";
    d.textContent = label;
    new window.maplibregl.Marker({ element: d })
      .setLngLat(lngLat)
      .setPopup(new window.maplibregl.Popup({ offset: 14 }).setHTML(sub))
      .addTo(map);
  }

  function weatherPopupHtml(p, legCode) {
    var rows = ["<strong>Météo · " + legCode + "</strong>"];
    if (p.t) { try { rows.push("<small>" + new Date(p.t).toLocaleString("fr-FR") + "</small>"); } catch (e) {} }
    if (p.wind_kn != null) rows.push("Vent : " + compass(p.wind_dir) + " " + (+p.wind_kn).toFixed(0) + " kn");
    if (p.current_kn != null) rows.push("Courant : " + compass(p.current_dir) + " " + (+p.current_kn).toFixed(1) + " kn");
    if (p.wave_m != null) rows.push("Houle : " + (+p.wave_m).toFixed(1) + " m" + (p.wave_period_s != null ? " · " + (+p.wave_period_s).toFixed(0) + " s" : ""));
    if (p.temp_c != null) rows.push("Temp. : " + (+p.temp_c).toFixed(1) + " °C");
    if (p.pressure_hpa != null) rows.push("Pression : " + (+p.pressure_hpa).toFixed(0) + " hPa");
    if (p.visibility_km != null) rows.push("Visi. : " + (+p.visibility_km).toFixed(0) + " km");
    if (rows.length <= 1) rows.push("<em>Données indisponibles</em>");
    return rows.join("<br>");
  }

  function addLeg(map, leg, idx, extend) {
    var color = leg.color || "#0D5966";
    var sid = "leg" + idx;
    var pts = (leg.points || []).filter(function (p) {
      return typeof p.lat === "number" && typeof p.lon === "number";
    });

    // Route théorique POL→POD (arc pointillé, couleur du leg, atténuée).
    if (leg.dep && leg.arr) {
      var theo = greatCircle([leg.dep.lon, leg.dep.lat], [leg.arr.lon, leg.arr.lat], 80);
      map.addSource(sid + "-theo", { type: "geojson",
        data: { type: "Feature", geometry: { type: "LineString", coordinates: theo } } });
      map.addLayer({ id: sid + "-theo", type: "line", source: sid + "-theo",
        paint: { "line-color": color, "line-width": 2, "line-dasharray": [2, 2], "line-opacity": 0.5 } });
      theo.forEach(extend);
    }

    // Trajet réel.
    if (pts.length) {
      var coords = pts.map(function (p) { return [p.lon, p.lat]; });
      map.addSource(sid + "-real", { type: "geojson",
        data: { type: "Feature", geometry: { type: "LineString", coordinates: coords } } });
      map.addLayer({ id: sid + "-real", type: "line", source: sid + "-real",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": color, "line-width": 3, "line-opacity": 0.9 } });

      map.addSource(sid + "-gps", { type: "geojson", data: { type: "FeatureCollection",
        features: pts.map(function (p, i) {
          return { type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
            properties: { idx: i, t: p.t || "", sog: p.sog == null ? "" : p.sog, leg: leg.leg_code } };
        }) } });
      map.addLayer({ id: sid + "-gps", type: "circle", source: sid + "-gps",
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 2, 8, 4],
          "circle-color": color, "circle-stroke-color": "#fff", "circle-stroke-width": 1, "circle-opacity": 0.9 } });
      map.on("click", sid + "-gps", function (e) {
        if (!e.features || !e.features[0]) return;
        var pr = e.features[0].properties, c = e.features[0].geometry.coordinates;
        var html = "<strong>" + pr.leg + " · point " + (parseInt(pr.idx, 10) + 1) + "</strong>";
        if (pr.t) { try { html += "<br><small>" + new Date(pr.t).toLocaleString("fr-FR") + "</small>"; } catch (x) {} }
        if (pr.sog !== "") html += "<br>SOG " + pr.sog + " kn";
        html += "<br><span style='font-family:monospace;color:#6E6E6E'>" + c[1].toFixed(4) + ", " + c[0].toFixed(4) + "</span>";
        new window.maplibregl.Popup({ offset: 10 }).setLngLat(c).setHTML(html).addTo(map);
      });
      map.on("mouseenter", sid + "-gps", function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", sid + "-gps", function () { map.getCanvas().style.cursor = ""; });
      coords.forEach(extend);
    }

    // Relevés météo historisés (cercles blancs cliquables).
    var wx = (leg.weather || []).filter(function (p) { return typeof p.lat === "number"; });
    if (wx.length) {
      map.addSource(sid + "-wx", { type: "geojson", data: { type: "FeatureCollection",
        features: wx.map(function (p) {
          return { type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: p };
        }) } });
      map.addLayer({ id: sid + "-wx", type: "circle", source: sid + "-wx",
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 3, 8, 6],
          "circle-color": "#ffffff", "circle-stroke-color": color, "circle-stroke-width": 2.5 } });
      map.on("click", sid + "-wx", function (e) {
        if (!e.features || !e.features[0]) return;
        var c = e.features[0].geometry.coordinates;
        new window.maplibregl.Popup({ offset: 12 }).setLngLat(c)
          .setHTML(weatherPopupHtml(e.features[0].properties, leg.leg_code)).addTo(map);
      });
      map.on("mouseenter", sid + "-wx", function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", sid + "-wx", function () { map.getCanvas().style.cursor = ""; });
    }

    if (leg.dep) {
      endpoint(map, [leg.dep.lon, leg.dep.lat], color, "POL",
        "<strong>" + (leg.dep.name || "") + "</strong> · " + leg.leg_code +
        "<br><span style='font-family:monospace'>" + (leg.dep.locode || "") + "</span>");
      extend([leg.dep.lon, leg.dep.lat]);
    }
    if (leg.arr) {
      endpoint(map, [leg.arr.lon, leg.arr.lat], color, "POD",
        "<strong>" + (leg.arr.name || "") + "</strong> · " + leg.leg_code +
        "<br><span style='font-family:monospace'>" + (leg.arr.locode || "") + "</span>");
      extend([leg.arr.lon, leg.arr.lat]);
    }
  }

  function initMap(el) {
    if (!el || el.dataset.navBound === "1") return true;
    if (typeof window.maplibregl === "undefined") return false;
    el.dataset.navBound = "1";

    var token = el.dataset.maptilerToken || "";
    var legs = (parse(el, "legs") || []);

    var mapStyle = el.dataset.mapStyle || "outdoor-v2";
    var style = token
      ? "https://api.maptiler.com/maps/" + encodeURIComponent(mapStyle) + "/style.json?key=" + encodeURIComponent(token)
      : "https://demotiles.maplibre.org/style.json";

    var map = new window.maplibregl.Map({
      container: el, style: style, center: [-30, 40], zoom: 2,
      attributionControl: { compact: true },
    });
    map.addControl(new window.maplibregl.NavigationControl({ visualizePitch: false }));

    map.on("load", function () {
      var bounds = null;
      var extend = function (c) {
        if (!c || typeof c[0] !== "number") return;
        if (!bounds) bounds = new window.maplibregl.LngLatBounds(c, c);
        else bounds.extend(c);
      };
      legs.forEach(function (leg, idx) { addLeg(map, leg, idx, extend); });

      if (bounds) {
        map.fitBounds(bounds, { padding: 70, maxZoom: 8, duration: 500 });
      } else {
        showEmptyNote(el);
      }
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
