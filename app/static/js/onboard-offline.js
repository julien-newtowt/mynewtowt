/*
 * File de soumission hors-ligne — espace bord (ARC-01 / EVO-05, liaisons satellite).
 *
 * Cible tout <form data-offline-queue> :
 *  - submit intercepté → POST fetch avec header x-csrf-token (cookie towt_csrf) ;
 *  - échec réseau (ou navigator.onLine === false) → l'entrée {url, fields,
 *    queued_at} est persistée dans IndexedDB (store « pending », via
 *    onboard-idb.js) ; repli localStorage si IndexedDB indisponible ;
 *  - une synchro en arrière-plan est demandée (Background Sync, tag
 *    "towt-onboard-flush") : le service worker rejoue la file même page fermée.
 *  - succès → suit la redirection (window.location = response.url).
 *
 * Au chargement, sur l'événement "online" et au message du SW, la file est
 * rejouée séquentiellement (retrait à chaque succès, arrêt au premier échec).
 * Le dédoublonnage est géré côté serveur via le champ caché client_uuid
 * (UUID généré ici), colonnes uniques noon_reports/watch_logs (migration 0023).
 */
(function () {
  "use strict";

  var QUEUE_KEY = "towt_offline_queue"; // repli si IndexedDB indisponible
  var SYNC_TAG = "towt-onboard-flush";
  var flushing = false;
  var idbOk = !!(window.towtIdb && window.towtIdb.available());

  /* ----- CSRF — même lecture du cookie que csrf-htmx.js ----- */

  function getCsrf() {
    var match = document.cookie.split("; ").find(function (r) {
      return r.indexOf("towt_csrf=") === 0;
    });
    return match ? match.split("=")[1] : null;
  }

  /* ----- localStorage (repli uniquement) ----- */

  function lsLoad() {
    try {
      var raw = localStorage.getItem(QUEUE_KEY);
      var q = raw ? JSON.parse(raw) : [];
      return Array.isArray(q) ? q : [];
    } catch (e) {
      return [];
    }
  }

  function lsSave(q) {
    try {
      localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
    } catch (e) {
      notify("Stockage local indisponible — saisie non conservée.", "error");
    }
  }

  /* ----- File unifiée (IndexedDB préféré, localStorage en repli) ----- */

  function qAdd(url, fields) {
    var entry = { url: url, fields: fields, queued_at: new Date().toISOString() };
    if (idbOk) {
      return window.towtIdb.enqueue(entry).catch(function () {
        // bascule de secours si l'écriture IDB échoue
        var q = lsLoad();
        q.push(entry);
        lsSave(q);
      });
    }
    var q = lsLoad();
    q.push(entry);
    lsSave(q);
    return Promise.resolve();
  }

  function qAll() {
    if (idbOk) {
      return window.towtIdb.all().catch(function () {
        return lsLoad();
      });
    }
    return Promise.resolve(lsLoad());
  }

  // Retire l'entrée traitée. En IndexedDB par clé `id` ; en localStorage on
  // retire la tête (flux séquentiel).
  function qRemove(entry) {
    if (idbOk && entry && typeof entry.id !== "undefined") {
      return window.towtIdb.remove(entry.id).catch(function () {});
    }
    var q = lsLoad();
    q.shift();
    lsSave(q);
    return Promise.resolve();
  }

  // Migre une éventuelle file localStorage historique vers IndexedDB (one-shot).
  function migrateLegacy() {
    if (!idbOk) return Promise.resolve();
    var legacy = lsLoad();
    if (!legacy.length) return Promise.resolve();
    return legacy
      .reduce(function (chain, e) {
        return chain.then(function () {
          return window.towtIdb.enqueue({
            url: e.url,
            fields: e.fields,
            queued_at: e.queued_at
          });
        });
      }, Promise.resolve())
      .then(function () {
        try {
          localStorage.removeItem(QUEUE_KEY);
        } catch (e) {
          /* ignore */
        }
      })
      .catch(function () {});
  }

  /* ----- Background Sync ----- */

  function requestSync() {
    if (!("serviceWorker" in navigator) || !("SyncManager" in window)) return;
    navigator.serviceWorker.ready
      .then(function (reg) {
        if (reg && reg.sync) {
          return reg.sync.register(SYNC_TAG);
        }
      })
      .catch(function () {
        /* sync indisponible — le rejeu page/online prend le relais */
      });
  }

  /* ----- Feedback utilisateur ----- */

  function notify(message, type) {
    if (typeof window.showToast === "function") {
      window.showToast(message, type || "info");
      return;
    }
    var banner = document.createElement("div");
    banner.setAttribute("role", "status");
    banner.style.cssText =
      "position:fixed;bottom:1rem;right:1rem;z-index:9999;max-width:22rem;" +
      "background:#0D5966;color:#fff;padding:.75rem 1rem;border-radius:8px;" +
      "font-family:inherit;box-shadow:0 4px 12px rgba(0,0,0,.25);";
    banner.textContent = message;
    document.body.appendChild(banner);
    setTimeout(function () {
      if (banner.parentNode) banner.parentNode.removeChild(banner);
    }, 6000);
  }

  /* ----- client_uuid (idempotence serveur) ----- */

  function makeUuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return "q-" + Date.now().toString(16) + "-" + Math.random().toString(16).slice(2, 14);
  }

  function ensureClientUuid(form) {
    var input = form.querySelector('input[name="client_uuid"]');
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "client_uuid";
      form.appendChild(input);
    }
    if (!input.value) input.value = makeUuid();
    return input;
  }

  /* ----- POST helper ----- */

  function postForm(url, body) {
    var headers = {};
    var csrf = getCsrf();
    if (csrf) headers["x-csrf-token"] = csrf;
    return fetch(url, {
      method: "POST",
      body: body,
      headers: headers,
      credentials: "same-origin"
    });
  }

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

  /* ----- Rejeu de la file (séquentiel, stop au premier échec) ----- */

  function flushQueue() {
    if (flushing) return;
    if (navigator.onLine === false) return;
    flushing = true;
    var sent = 0;

    function done() {
      flushing = false;
      if (sent > 0) {
        notify(sent + " saisie(s) hors-ligne synchronisée(s).", "success");
      }
    }

    function step() {
      qAll().then(function (q) {
        if (!q.length) {
          done();
          return;
        }
        var entry = q[0];
        postForm(entry.url, entryToFormData(entry))
          .then(function (resp) {
            if (isRealSuccess(resp)) {
              qRemove(entry).then(function () {
                sent += 1;
                step();
              });
            } else {
              done(); // serveur joignable mais refus — on retentera plus tard
            }
          })
          .catch(function () {
            done(); // toujours hors-ligne — stop, on garde la file
          });
      });
    }

    step();
  }

  /* ----- Interception des forms data-offline-queue ----- */

  function bindForm(form) {
    form.addEventListener("submit", function (evt) {
      evt.preventDefault();
      ensureClientUuid(form);

      var url = form.getAttribute("action") || window.location.href;
      var fd = new FormData(form);
      var fields = {};
      fd.forEach(function (value, key) {
        if (typeof value === "string") fields[key] = value;
      });

      function queueIt() {
        qAdd(url, fields).then(function () {
          requestSync();
          notify(
            "Hors ligne — saisie conservée localement, synchronisation au retour du réseau.",
            "warn"
          );
          form.reset();
        });
      }

      if (navigator.onLine === false) {
        queueIt();
        return;
      }

      postForm(url, fd)
        .then(function (resp) {
          if (isRealSuccess(resp)) {
            if (resp.url) {
              window.location = resp.url;
            } else {
              window.location.reload();
            }
          } else if (resp.url && resp.url.indexOf("/login") !== -1) {
            queueIt(); // session expirée : on conserve puis ré-authentifie
            window.location = resp.url;
          } else {
            notify("Échec de l'envoi (HTTP " + resp.status + ").", "error");
          }
        })
        .catch(function () {
          queueIt();
        });
    });
  }

  /* ----- Init ----- */

  function init() {
    var forms = document.querySelectorAll("form[data-offline-queue]");
    Array.prototype.forEach.call(forms, bindForm);
    window.addEventListener("online", flushQueue);
    // Le SW signale la fin d'un flush en arrière-plan → on rafraîchit le toast/état.
    if ("serviceWorker" in navigator && navigator.serviceWorker) {
      navigator.serviceWorker.addEventListener("message", function (evt) {
        if (evt.data && evt.data.type === "towt-flushed" && evt.data.count > 0) {
          notify(evt.data.count + " saisie(s) synchronisée(s) en arrière-plan.", "success");
        }
      });
    }
    migrateLegacy().then(flushQueue);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
