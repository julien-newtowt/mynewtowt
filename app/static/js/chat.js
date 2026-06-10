/*
 * Chat Kairos AI — SPA-light client.
 *
 * Compatible CSP strict : chargé en fichier externe (pas d'inline script).
 * Soumet le formulaire en POST /chat/messages avec le double-submit CSRF
 * (header x-csrf-token + cookie towt_csrf) et bulle la réponse JSON.
 */
(function () {
  "use strict";

  function init() {
    var form = document.getElementById("chat-form");
    var history = document.getElementById("chat-history");
    if (!form || !history) return;

    function append(role, content) {
      var wrap = document.createElement("div");
      wrap.style.cssText =
        "display:flex;gap:8px;margin-bottom:12px;" +
        (role === "user" ? "flex-direction:row-reverse;" : "");
      var bub = document.createElement("div");
      bub.style.cssText =
        "max-width:75%;padding:12px;border-radius:10px;white-space:pre-wrap;" +
        (role === "user"
          ? "background:#0D5966;color:#fff;"
          : "background:#F8F2E6;color:#2A2A2A;");
      bub.textContent = content;
      wrap.appendChild(bub);
      history.appendChild(wrap);
      history.scrollTop = history.scrollHeight;
    }

    function csrfToken() {
      var match = document.cookie.match(/(?:^|;\s*)towt_csrf=([^;]+)/);
      return match ? match[1] : "";
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var input = document.getElementById("chat-input");
      var text = (input.value || "").trim();
      if (!text) return;
      append("user", text);
      input.value = "";
      var fd = new FormData(form);
      fd.set("text", text);
      fetch("/chat/messages", {
        method: "POST",
        headers: { "x-csrf-token": csrfToken() },
        body: fd,
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          append(data.role || "assistant", data.content || "(vide)");
        })
        .catch(function () {
          append("assistant", "Erreur réseau.");
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
