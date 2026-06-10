/*
 * lang-switch.js — Client-side language cookie setter.
 *
 * Listens for clicks on [data-lang] elements (the lang-switch link in the
 * public header). On click:
 *   1. Sets `towt_lang` cookie directly in the browser (no round-trip needed).
 *   2. Reloads the current page so the server-rendered Jinja2 templates pick
 *      up the new `lang` value from the context processor.
 *
 * The <a> href="/lang/..." fallback still works for no-JS environments.
 * towt_lang is httponly=False so JS can write it.
 */
(function () {
  "use strict";

  function setCookie(name, value, days) {
    var expires = "; max-age=" + (days * 86400);
    var secure = location.protocol === "https:" ? "; secure" : "";
    document.cookie = name + "=" + value + expires + "; path=/; samesite=lax" + secure;
  }

  function init() {
    document.querySelectorAll("[data-lang]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        var lang = el.getAttribute("data-lang");
        if (lang) {
          setCookie("towt_lang", lang, 365);
          window.location.reload();
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
