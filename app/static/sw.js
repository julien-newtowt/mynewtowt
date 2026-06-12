/*
 * NEWTOWT Bord — service worker (ARC-01 PWA offline pour le bord).
 *
 * Stratégie : network-first avec repli cache pour les GET same-origin
 * sur /onboard* et /static/*. Les navigations sans réseau ni cache
 * tombent sur /static/offline.html. Les POST ne sont JAMAIS interceptés
 * (la file offline est gérée par onboard-offline.js + localStorage).
 *
 * Script classique (pas de module) — servi par pwa_router avec le header
 * Service-Worker-Allowed: / pour un scope racine.
 */
/* eslint-disable no-restricted-globals */

var CACHE_NAME = "towt-onboard-v1";

/* App shell : CSS + JS chargés par les pages /onboard (base.html +
 * staff/_layout.html) + logo sidebar + page de repli hors-ligne. */
var PRECACHE = [
  "/static/css/tokens.css",
  "/static/css/kairos.css",
  "/static/js/csrf-htmx.js",
  "/static/js/lucide-init.js",
  "/static/js/toast.js",
  "/static/js/modal.js",
  "/static/js/forms.js",
  "/static/js/towt-tz.js",
  "/static/js/sidebar.js",
  "/static/js/clock.js",
  "/static/js/topbar-menus.js",
  "/static/js/pwa-onboard.js",
  "/static/js/onboard-offline.js",
  "/static/img/logo_NEWTOWT_web_white.png",
  "/static/offline.html"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then(function (cache) {
        return cache.addAll(PRECACHE);
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (key) {
              return key !== CACHE_NAME;
            })
            .map(function (key) {
              return caches.delete(key);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

function shouldHandle(request) {
  if (request.method !== "GET") return false; // jamais les POST/PUT/DELETE
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return false; // same-origin only
  return (
    url.pathname === "/onboard" ||
    url.pathname.indexOf("/onboard/") === 0 ||
    url.pathname.indexOf("/static/") === 0
  );
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (!shouldHandle(request)) return;

  event.respondWith(
    fetch(request)
      .then(function (response) {
        // Network-first : on met en cache les réponses 200 (clone).
        if (response && response.status === 200 && response.type === "basic") {
          var copy = response.clone();
          caches.open(CACHE_NAME).then(function (cache) {
            cache.put(request, copy);
          });
        }
        return response;
      })
      .catch(function () {
        // Repli cache, puis offline.html pour les navigations.
        return caches.match(request).then(function (cached) {
          if (cached) return cached;
          if (request.mode === "navigate") {
            return caches.match("/static/offline.html");
          }
          return Response.error();
        });
      })
  );
});
