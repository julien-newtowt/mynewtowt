/*
 * WebAuthn / Passkey bridge — NEWTOWT.
 *
 * Expose ``window.TOWT_WEBAUTHN`` :
 *   - register(beginUrl, verifyUrl, {name})      → enregistre une nouvelle passkey
 *   - authenticate(beginUrl, verifyUrl)          → challenge login
 *
 * Compatible CSP strict : pas d'inline, chargé via ``<script src=...>``.
 * Tous les échanges serveur sont en JSON ; le CSRF token est envoyé en
 * header ``x-csrf-token`` (lu depuis cookie towt_csrf).
 */
(function () {
  "use strict";

  // ── Encoding helpers (base64url ↔ ArrayBuffer) ────────────────────
  function b64urlToBuf(s) {
    s = String(s).replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    var bin = atob(s);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  function bufToB64url(buf) {
    var bytes = new Uint8Array(buf);
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function csrfToken() {
    var m = document.cookie.match(/(?:^|;\s*)towt_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  // Convertit les options serveur (champs base64url string) → format
  // requis par navigator.credentials.create/get (ArrayBuffer).
  function decodeRegOptions(opts) {
    opts.challenge = b64urlToBuf(opts.challenge);
    opts.user.id = b64urlToBuf(opts.user.id);
    if (opts.excludeCredentials) {
      opts.excludeCredentials = opts.excludeCredentials.map(function (c) {
        return { id: b64urlToBuf(c.id), type: c.type, transports: c.transports };
      });
    }
    return opts;
  }

  function decodeAuthOptions(opts) {
    opts.challenge = b64urlToBuf(opts.challenge);
    if (opts.allowCredentials) {
      opts.allowCredentials = opts.allowCredentials.map(function (c) {
        return { id: b64urlToBuf(c.id), type: c.type, transports: c.transports };
      });
    }
    return opts;
  }

  // Sérialise un PublicKeyCredential (de create() ou get()) en JSON
  // pour envoi au serveur. La lib webauthn Python attend ce format.
  function serializeCredential(cred) {
    var json = {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || null,
      clientExtensionResults: cred.getClientExtensionResults
        ? cred.getClientExtensionResults() : {},
      response: {},
    };
    var r = cred.response;
    // attestationResponse (register)
    if (r.attestationObject) {
      json.response.attestationObject = bufToB64url(r.attestationObject);
      json.response.clientDataJSON = bufToB64url(r.clientDataJSON);
      if (typeof r.getTransports === "function") {
        try { json.response.transports = r.getTransports(); } catch (e) {}
      }
    }
    // assertionResponse (auth)
    if (r.authenticatorData) {
      json.response.authenticatorData = bufToB64url(r.authenticatorData);
      json.response.clientDataJSON = bufToB64url(r.clientDataJSON);
      json.response.signature = bufToB64url(r.signature);
      if (r.userHandle) json.response.userHandle = bufToB64url(r.userHandle);
    }
    return json;
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "content-type": "application/json",
        "x-csrf-token": csrfToken(),
      },
      body: JSON.stringify(body || {}),
    });
  }

  // ── Register ──────────────────────────────────────────────────────
  async function register(beginUrl, verifyUrl, opts) {
    if (!window.PublicKeyCredential) {
      throw new Error("Votre navigateur ne supporte pas WebAuthn.");
    }
    opts = opts || {};
    var r = await postJson(beginUrl, { name: opts.name || "" });
    if (!r.ok) throw new Error("Échec init register (" + r.status + ")");
    var publicKey = decodeRegOptions(await r.json());

    var cred;
    try {
      cred = await navigator.credentials.create({ publicKey: publicKey });
    } catch (e) {
      throw new Error("Création annulée ou refusée : " + (e.message || e.name));
    }

    var payload = serializeCredential(cred);
    var v = await postJson(verifyUrl, {
      credential: payload,
      name: opts.name || "",
    });
    if (!v.ok) {
      var detail = await v.text().catch(function () { return ""; });
      throw new Error("Verify a échoué (" + v.status + ") " + detail);
    }
    return await v.json();
  }

  // ── Authenticate ──────────────────────────────────────────────────
  async function authenticate(beginUrl, verifyUrl) {
    if (!window.PublicKeyCredential) {
      throw new Error("Votre navigateur ne supporte pas WebAuthn.");
    }
    var r = await postJson(beginUrl, {});
    if (!r.ok) throw new Error("Échec init auth (" + r.status + ")");
    var publicKey = decodeAuthOptions(await r.json());

    var cred;
    try {
      cred = await navigator.credentials.get({ publicKey: publicKey });
    } catch (e) {
      throw new Error("Authentification annulée ou refusée : " + (e.message || e.name));
    }

    var payload = serializeCredential(cred);
    var v = await postJson(verifyUrl, { credential: payload });
    if (!v.ok) {
      var detail = await v.text().catch(function () { return ""; });
      throw new Error("Verify a échoué (" + v.status + ") " + detail);
    }
    return await v.json();
  }

  window.TOWT_WEBAUTHN = {
    register: register,
    authenticate: authenticate,
    isSupported: function () { return !!window.PublicKeyCredential; },
  };
})();
