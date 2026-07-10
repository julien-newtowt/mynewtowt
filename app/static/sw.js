/*
 * NEWTOWT Bord — service worker (ARC-01 PWA offline pour le bord).
 *
 * Stratégie : network-first avec repli cache pour les GET same-origin
 * sur /onboard* et /static/*. Les navigations sans réseau ni cache
 * tombent sur /static/offline.html. Les POST ne sont JAMAIS interceptés
 * (la file offline est persistée en IndexedDB par onboard-offline.js).
 *
 * EVO-05 — Background Sync : l'événement "sync" (tag towt-onboard-flush)
 * rejoue la file IndexedDB vers les endpoints POST même page fermée, dès que
 * la connectivité revient. Le dédoublonnage reste garanti côté serveur
 * (client_uuid). Le store IndexedDB est partagé via onboard-idb.js.
 *
 * Script classique (pas de module) — servi par pwa_router avec le header
 * Service-Worker-Allowed: / pour un scope racine.
 */
/* eslint-disable no-restricted-globals */

importScripts("/static/js/onboard-idb.js");

var CACHE_NAME = "towt-onboard-v2";
var SYNC_TAG = "towt-onboard-flush";

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
  "/static/js/onboard-idb.js",
  "/static/js/event-autosave.js",
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

/* ----- EVO-05 : Background Sync — rejeu de la file IndexedDB ----- */

// Succès réel = 2xx ET pas une redirection vers /login (session expirée).
function isRealSuccess(resp) {
  return resp.ok && !(resp.url && resp.url.indexOf("/login") !== -1);
}

function entryToFormData(entry) {
  var body = new FormData();
  var fields = entry.fields || {};
  Object.keys(fields).forEach(function (k) {
    body.append(k, fields[k]);
  });
  return body;
}

// Rejoue la file séquentiellement ; retire chaque entrée sur succès, s'arrête
// au premier échec (toujours hors-ligne) en conservant le reste. Le jeton CSRF
// (_csrf) est porté par les `fields`, et le cookie part automatiquement
// (credentials), donc pas besoin de lire le cookie (indisponible dans le SW).
function flushPending() {
  if (!self.towtIdb) return Promise.resolve(0);
  var sent = 0;

  function step() {
    return self.towtIdb.all().then(function (entries) {
      if (!entries.length) return sent;
      var entry = entries[0];
      return fetch(entry.url, {
        method: "POST",
        body: entryToFormData(entry),
        headers: {},
        credentials: "same-origin"
      })
        .then(function (resp) {
          if (isRealSuccess(resp)) {
            return self.towtIdb.remove(entry.id).then(function () {
              sent += 1;
              return step();
            });
          }
          return sent; // serveur joignable mais refus — on retentera
        })
        .catch(function () {
          return sent; // toujours hors-ligne — on garde la file
        });
    });
  }

  return step();
}

// Notifie les pages ouvertes du nombre de saisies synchronisées.
function notifyClients(count) {
  if (!count) return Promise.resolve();
  return self.clients.matchAll({ includeUncontrolled: true }).then(function (list) {
    list.forEach(function (client) {
      client.postMessage({ type: "towt-flushed", count: count });
    });
  });
}

self.addEventListener("sync", function (event) {
  if (event.tag === SYNC_TAG) {
    event.waitUntil(
      flushPending().then(function (count) {
        return notifyClients(count);
      })
    );
  }
});
