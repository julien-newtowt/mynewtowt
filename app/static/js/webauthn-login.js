/*
 * Bouton "Se connecter avec une passkey" sur les pages /login et /me/login.
 *
 * Lit les URLs sur ``#wa-login-btn`` (data-attributes), lance
 * TOWT_WEBAUTHN.authenticate, redirige vers la response.redirect.
 */
(function () {
  "use strict";

  function init() {
    var btn = document.getElementById("wa-login-btn");
    var status = document.getElementById("wa-login-status");
    if (!btn || !window.TOWT_WEBAUTHN) return;

    if (!window.TOWT_WEBAUTHN.isSupported()) {
      btn.style.display = "none";
      return;
    }

    btn.addEventListener("click", function () {
      var begin = btn.getAttribute("data-begin-url");
      var verify = btn.getAttribute("data-verify-url");
      btn.disabled = true;
      if (status) status.textContent = "Touchez votre clé ou utilisez la biométrie…";
      window.TOWT_WEBAUTHN.authenticate(begin, verify)
        .then(function (res) {
          if (status) status.textContent = "✓ Authentifié — redirection…";
          window.location.href = res.redirect || "/";
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
