/*
 * File de soumission hors-ligne — espace bord (ARC-01, liaisons satellite).
 *
 * Cible tout <form data-offline-queue> :
 *  - submit intercepté → POST fetch avec header x-csrf-token (cookie
 *    towt_csrf, même lecture que csrf-htmx.js) ;
 *  - échec réseau (ou navigator.onLine === false) → l'entrée
 *    {url, fields, queued_at} est poussée dans localStorage
 *    ("towt_offline_queue"), toast d'information, form reset ;
 *  - succès → suit la redirection (window.location = response.url).
 *
 * Au chargement de page et sur l'événement "online", la file est rejouée
 * séquentiellement : retrait à chaque succès, arrêt au premier échec.
 * Le dédoublonnage est géré côté serveur via le champ caché client_uuid
 * (UUID généré ici), colonnes uniques noon_reports/watch_logs.
 */
(function () {
  "use strict";

  var QUEUE_KEY = "towt_offline_queue";
  var flushing = false;

  /* ----- CSRF — même lecture du cookie que csrf-htmx.js ----- */

  function getCsrf() {
    var match = document.cookie.split("; ").find(function (r) {
      return r.indexOf("towt_csrf=") === 0;
    });
    return match ? match.split("=")[1] : null;
  }

  /* ----- localStorage queue ----- */

  function loadQueue() {
    try {
      var raw = localStorage.getItem(QUEUE_KEY);
      var q = raw ? JSON.parse(raw) : [];
      return Array.isArray(q) ? q : [];
    } catch (e) {
      return [];
    }
  }

  function saveQueue(q) {
    try {
      localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
    } catch (e) {
      notify("Stockage local indisponible — saisie non conservée.", "error");
    }
  }

  function enqueue(url, fields) {
    var q = loadQueue();
    q.push({ url: url, fields: fields, queued_at: new Date().toISOString() });
    saveQueue(q);
  }

  /* ----- Feedback utilisateur ----- */

  function notify(message, type) {
    if (typeof window.showToast === "function") {
      window.showToast(message, type || "info");
      return;
    }
    // Repli minimal si toast.js absent (page servie depuis le cache SW).
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

  /* ----- POST helper ----- */

  function makeUuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return (
      "q-" + Date.now().toString(16) + "-" + Math.random().toString(16).slice(2, 14)
    );
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

  // Succès réel = 2xx ET pas une redirection suivie vers /login
  // (session expirée : la sauvegarde n'a PAS eu lieu).
  function isRealSuccess(resp) {
    return resp.ok && !(resp.url && resp.url.indexOf("/login") !== -1);
  }

  /* ----- Rejeu de la file (séquentiel, stop au premier échec) ----- */

  function flushQueue() {
    if (flushing) return;
    if (navigator.onLine === false) return;
    if (!loadQueue().length) return;
    flushing = true;
    var sent = 0;

    function done() {
      flushing = false;
      if (sent > 0) {
        notify(sent + " saisie(s) hors-ligne synchronisée(s).", "success");
      }
    }

    function step() {
      var q = loadQueue();
      if (!q.length) {
        done();
        return;
      }
      var entry = q[0];
      var body = new FormData();
      Object.keys(entry.fields || {}).forEach(function (k) {
        body.append(k, entry.fields[k]);
      });
      postForm(entry.url, body)
        .then(function (resp) {
          if (isRealSuccess(resp)) {
            var rest = loadQueue();
            rest.shift();
            saveQueue(rest);
            sent += 1;
            step();
          } else {
            done(); // serveur joignable mais refus — on retentera plus tard
          }
        })
        .catch(function () {
          done(); // toujours hors-ligne — stop, on garde la file
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
        enqueue(url, fields);
        notify(
          "Hors ligne — saisie conservée localement, synchronisation au retour du réseau.",
          "warn"
        );
        form.reset();
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
            // Session expirée : on conserve la saisie puis on ré-authentifie.
            queueIt();
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
    flushQueue();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
