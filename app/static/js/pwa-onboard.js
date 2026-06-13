/*
 * PWA NEWTOWT Bord — enregistrement du service worker (ARC-01).
 *
 * Fichier externe (CSP stricte — pas de JS inline). Le SW est servi par
 * pwa_router sur /sw.js avec Service-Worker-Allowed: / (scope racine,
 * nécessaire pour contrôler /onboard*).
 */
(function () {
  "use strict";

  if (!("serviceWorker" in navigator)) return;

  window.addEventListener("load", function () {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .catch(function (err) {
        if (window.console && console.warn) {
          console.warn("SW NEWTOWT Bord non enregistré :", err);
        }
      });
  });
})();
