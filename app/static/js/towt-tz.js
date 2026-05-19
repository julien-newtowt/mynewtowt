/*
 * TOWT timezone helpers — for forms that need to record a local port time
 * with its IANA timezone and display an immediate UTC preview.
 *
 * Markup convention:
 *   <div class="tz-input-wrap">
 *     <input type="time" name="etd_time" />
 *     <select class="tz-select" name="etd_tz">
 *       <option value="port_local">Port local</option>
 *       <option value="Europe/Paris">Paris</option>
 *       <option value="UTC">UTC</option>
 *     </select>
 *   </div>
 *   <small class="tz-utc-hint"></small>
 *
 * `port_local` resolves to the value found on any element carrying a
 * `data-port-tz` attribute (typically the sidebar clock when the staff
 * layout exposes the next port). Falls back to UTC silently.
 */
(function () {
  "use strict";

  function getPortTz() {
    var el = document.querySelector("[data-port-tz]");
    if (el && el.dataset && el.dataset.portTz) return el.dataset.portTz;
    return "UTC";
  }

  function offsetMinutes(tz, date) {
    try {
      var d = date || new Date();
      var utcStr = d.toLocaleString("en-US", { timeZone: "UTC" });
      var tzStr = d.toLocaleString("en-US", { timeZone: tz });
      return Math.round((new Date(tzStr) - new Date(utcStr)) / 60000);
    } catch (e) { return 0; }
  }

  function resolve(tz) {
    return tz === "port_local" ? getPortTz() : tz;
  }

  function convertTime(timeStr, fromTz, toTz, refDate) {
    if (!timeStr || timeStr.indexOf(":") < 0) return timeStr;
    try {
      var parts = timeStr.split(":");
      var h = parseInt(parts[0], 10), m = parseInt(parts[1], 10);
      var d = refDate ? new Date(refDate) : new Date();
      var diff = offsetMinutes(toTz, d) - offsetMinutes(fromTz, d);
      var total = ((h * 60 + m + diff) % 1440 + 1440) % 1440;
      var nh = Math.floor(total / 60), nm = total % 60;
      return (nh < 10 ? "0" : "") + nh + ":" + (nm < 10 ? "0" : "") + nm;
    } catch (e) { return timeStr; }
  }

  function convertDatetime(dtStr, fromTz, toTz) {
    if (!dtStr) return dtStr;
    try {
      var ref = new Date(dtStr);
      var diff = (offsetMinutes(toTz, ref) - offsetMinutes(fromTz, ref)) * 60000;
      var d = new Date(ref.getTime() + diff);
      var pad = function (n) { return (n < 10 ? "0" : "") + n; };
      return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate())
        + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
    } catch (e) { return dtStr; }
  }

  function utcLabel(timeStr, tzName, refDate) {
    if (!timeStr || tzName === "UTC") return "";
    var t = convertTime(timeStr, resolve(tzName), "UTC", refDate);
    return t + " UTC";
  }

  function updateHint(wrap) {
    var inp = wrap.querySelector('input[type="time"], input[type="datetime-local"]');
    var sel = wrap.querySelector(".tz-select");
    var hint = wrap.querySelector(".tz-utc-hint")
      || (wrap.parentElement && wrap.parentElement.querySelector(".tz-utc-hint"));
    if (!hint || !inp || !inp.value) return;
    var tzVal = sel ? sel.value : "UTC";
    if (inp.type === "time") {
      hint.textContent = utcLabel(inp.value, tzVal);
    } else {
      var dt = convertDatetime(inp.value, resolve(tzVal), "UTC");
      hint.textContent = dt ? dt.replace("T", " ") + " UTC" : "";
    }
  }

  function bind() {
    document.querySelectorAll(".tz-input-wrap").forEach(function (wrap) {
      if (wrap.dataset.tzBound === "1") return;
      wrap.dataset.tzBound = "1";
      var update = function () { updateHint(wrap); };
      wrap.addEventListener("change", update);
      wrap.addEventListener("input", update);
      update();
    });
  }

  window.TOWT_TZ = {
    convertTime: convertTime,
    convertDatetime: convertDatetime,
    resolve: resolve,
    utcLabel: utcLabel,
    portTz: getPortTz,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
  document.body && document.body.addEventListener("htmx:afterSwap", bind);
})();
