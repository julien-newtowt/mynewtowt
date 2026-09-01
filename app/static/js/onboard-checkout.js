/*
 * Page « Lien de paiement CB » — sélection de l'URL et suivi du règlement.
 *
 * Deux défauts relevés à l'audit du 2026-08-27 :
 *
 * 1. le champ URL se sélectionnait par un `onclick` inline, inerte sous la CSP
 *    stricte du projet — cliquer ne faisait rien ;
 * 2. la page annonçait « se met à jour après paiement » alors qu'aucun
 *    rafraîchissement n'existait : le commandant devait deviner quand revenir
 *    et recharger à la main, devant le client.
 *
 * Le serveur réconcilie déjà le paiement à l'ouverture de la fiche de vente
 * (`_reconcile_pending_card_payment`). Il suffit donc de la rouvrir
 * périodiquement : on interroge la fiche, et dès qu'elle n'est plus « en
 * attente », on y renvoie l'utilisateur.
 */
(function () {
  "use strict";

  /* ----- Sélection de l'URL au focus (remplace le onclick inline) ----- */

  var urlField = document.querySelector("[data-select-on-focus]");
  if (urlField) {
    urlField.addEventListener("focus", function () {
      urlField.select();
    });
    urlField.addEventListener("click", function () {
      urlField.select();
    });
  }

  /* ----- Suivi du paiement ----- */

  var watcher = document.querySelector("[data-checkout-watch]");
  if (!watcher) return;

  var saleUrl = watcher.getAttribute("data-sale-url");
  var status = document.querySelector("[data-checkout-status]");
  if (!saleUrl) return;

  var INTERVAL_MS = 8000;
  // Garde-fou : la page peut rester ouverte des heures sur une vente jamais
  // payée. On cesse d'interroger au bout de 30 min plutôt que de marteler le
  // serveur — et Stripe, que chaque vérification interroge à son tour.
  var MAX_ATTEMPTS = 225;
  var attempts = 0;
  var checking = false;

  function announce(message) {
    if (status) status.textContent = message;
  }

  function check() {
    if (checking || navigator.onLine === false) return;
    attempts += 1;
    if (attempts > MAX_ATTEMPTS) {
      window.clearInterval(timer);
      announce("Suivi interrompu — rechargez la page pour reprendre.");
      return;
    }
    checking = true;
    fetch(saleUrl, { credentials: "same-origin", headers: { accept: "text/html" } })
      .then(function (resp) {
        checking = false;
        if (!resp.ok) return null;
        return resp.text();
      })
      .then(function (html) {
        if (!html) return;
        // La fiche de vente affiche « Vente réglée » une fois le paiement
        // confirmé — c'est le signal le plus simple et le moins couplé.
        if (html.indexOf("Vente réglée") !== -1) {
          window.clearInterval(timer);
          announce("Paiement confirmé — redirection…");
          window.location = saleUrl;
        }
      })
      .catch(function () {
        checking = false; // hors ligne ou serveur injoignable : on retentera
      });
  }

  var timer = window.setInterval(check, INTERVAL_MS);
  window.setTimeout(check, 2000);
})();
