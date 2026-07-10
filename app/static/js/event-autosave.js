/*
 * Autosave du wizard de saisie d'événement MRV (LOT 4).
 *
 * Cible le <form data-autosave-url="/onboard/events/{id}/autosave"> du wizard :
 *  - POST périodique (30 s) tant que le formulaire a changé depuis le dernier
 *    enregistrement, + POST débounced (2,5 s) sur tout `input`/`change` ;
 *  - le corps est le FormData complet (mêmes champs que la soumission) — le
 *    serveur met à jour le brouillon + last_saved_at et répond 204 ;
 *  - indicateur discret « Enregistré à HH:MM » dans [data-autosave-status].
 *
 * CSP stricte : fichier externe, aucun script inline. Le jeton CSRF est lu
 * dans le cookie towt_csrf (même lecture que csrf-htmx.js / onboard-offline.js)
 * et envoyé en header x-csrf-token.
 */
(function () {
  "use strict";

  var PERIODIC_MS = 30000; // filet périodique
  var DEBOUNCE_MS = 2500; // après une frappe

  function getCsrf() {
    var match = document.cookie.split("; ").find(function (r) {
      return r.indexOf("towt_csrf=") === 0;
    });
    return match ? match.split("=")[1] : null;
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function nowLabel() {
    var d = new Date();
    return pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function setStatus(form, text, kind) {
    var el = form.querySelector("[data-autosave-status]");
    if (!el) return;
    el.textContent = text;
    el.setAttribute("data-autosave-state", kind || "");
  }

  function bind(form) {
    if (form.dataset.autosaveBound === "1") return;
    form.dataset.autosaveBound = "1";
    var url = form.getAttribute("data-autosave-url");
    if (!url) return;

    var dirty = false;
    var inflight = false;
    var debounceTimer = null;

    function save() {
      if (inflight || !dirty) return;
      if (typeof navigator !== "undefined" && navigator.onLine === false) return;
      inflight = true;
      var savedDirty = dirty;
      dirty = false;
      setStatus(form, "Enregistrement…", "saving");
      var headers = {};
      var csrf = getCsrf();
      if (csrf) headers["x-csrf-token"] = csrf;
      fetch(url, {
        method: "POST",
        body: new FormData(form),
        headers: headers,
        credentials: "same-origin"
      })
        .then(function (resp) {
          inflight = false;
          if (resp.status === 204 || (resp.ok && resp.status < 300)) {
            setStatus(form, "Enregistré à " + nowLabel(), "saved");
          } else if (resp.status === 403) {
            dirty = savedDirty;
            setStatus(form, "Reprise réservée à l'auteur — non enregistré.", "error");
          } else {
            dirty = savedDirty; // on retentera
            setStatus(form, "Enregistrement différé…", "error");
          }
        })
        .catch(function () {
          inflight = false;
          dirty = savedDirty; // hors-ligne : on retentera
          setStatus(form, "Hors ligne — sera enregistré au retour du réseau.", "error");
        });
    }

    function markDirty() {
      dirty = true;
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(save, DEBOUNCE_MS);
    }

    form.addEventListener("input", markDirty);
    form.addEventListener("change", markDirty);
    setInterval(save, PERIODIC_MS);
  }

  function init() {
    var forms = document.querySelectorAll("form[data-autosave-url]");
    Array.prototype.forEach.call(forms, bind);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
