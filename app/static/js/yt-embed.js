/* Façade YouTube click-to-load (CSP stricte + confidentialité).
   Aucune requête vers YouTube tant que l'utilisateur n'a pas cliqué : la
   façade n'affiche qu'un poster local. Au clic, elle est remplacée par un
   iframe youtube-nocookie.com (mode confidentialité renforcée), seul domaine
   d'embed autorisé par la CSP (frame-src, cf. security_headers.py). */
(function () {
  "use strict";

  function activate(el) {
    var id = el.getAttribute("data-yt-id") || "";
    if (!/^[A-Za-z0-9_-]{6,20}$/.test(id)) return;
    var iframe = document.createElement("iframe");
    iframe.src =
      "https://www.youtube-nocookie.com/embed/" + id + "?autoplay=1&rel=0";
    iframe.title = el.getAttribute("data-yt-title") || "";
    iframe.setAttribute(
      "allow",
      "autoplay; encrypted-media; picture-in-picture; web-share"
    );
    iframe.setAttribute("allowfullscreen", "");
    iframe.className = "yt-embed";
    el.replaceWith(iframe);
    iframe.focus();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var facades = document.querySelectorAll("button.yt-facade[data-yt-id]");
    facades.forEach(function (el) {
      el.addEventListener(
        "click",
        function () {
          activate(el);
        },
        { once: true }
      );
    });
  });
})();
