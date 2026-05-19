/*
 * Glue minimaliste pour /me/account/webauthn et /admin/my-account/webauthn :
 * lit les URLs sur le bouton ``#wa-add-btn`` (data-attributes), lance
 * TOWT_WEBAUTHN.register, affiche le statut dans ``#wa-status``, recharge
 * la page sur succès.
 */
(function () {
  "use strict";

  function init() {
    var btn = document.getElementById("wa-add-btn");
    var status = document.getElementById("wa-status");
    var nameInput = document.getElementById("wa-name");
    if (!btn || !window.TOWT_WEBAUTHN) return;

    if (!window.TOWT_WEBAUTHN.isSupported()) {
      btn.disabled = true;
      if (status) {
        status.textContent =
          "Votre navigateur ne supporte pas WebAuthn (PublicKeyCredential).";
      }
      return;
    }

    btn.addEventListener("click", function () {
      var begin = btn.getAttribute("data-begin-url");
      var verify = btn.getAttribute("data-verify-url");
      var listUrl = btn.getAttribute("data-list-url") || window.location.pathname;
      var name = nameInput ? nameInput.value : "";
      btn.disabled = true;
      if (status) status.textContent = "Approuvez l'enregistrement sur votre appareil…";
      window.TOWT_WEBAUTHN.register(begin, verify, { name: name })
        .then(function (res) {
          if (status) status.textContent = "✓ Passkey enregistrée — rechargement…";
          setTimeout(function () { window.location.href = listUrl; }, 600);
        })
        .catch(function (err) {
          btn.disabled = false;
          if (status) status.textContent = "Erreur : " + (err.message || err);
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
