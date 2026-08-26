"""Signature électronique de la booking note via Yousign (niveau **avancé**).

Calqué sur le patron maison de ``services.stripe_checkout`` — c'est la seule
intégration signée déjà en production, et la reproduire évite d'inventer une
seconde manière de faire la même chose.

Quatre principes, dans cet ordre :

1. **Secure-by-default.** Sans ``YOUSIGN_API_KEY``, la voie électronique est
   indisponible et l'appelant répond 503. Le circuit dégradé — signature
   manuscrite du document Word — reste seul actif. Jamais de repli silencieux
   vers « signé ».
2. **La confiance vient de la signature du webhook, pas de son contenu.**
   L'empreinte HMAC-SHA256 est vérifiée sur le **corps brut**, avant tout
   parsing, en comparaison à temps constant.
3. **On ne croit pas le payload sur l'état métier.** Un webhook déclenche une
   **re-lecture serveur-à-serveur** de la procédure chez Yousign ; c'est cette
   réponse qui fait foi. Un webhook forgé ne peut donc pas faire passer une
   booking note pour signée.
4. **Idempotence.** Rejouer un événement ne change rien : la transition n'est
   appliquée que si l'état diffère.

Niveau de signature : ``electronic_signature`` (avancé au sens eIDAS), pas
simple. La booking note engage un montant commercial : l'identification du
signataire et le scellement du document doivent être opposables.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("yousign")

API_BASE = "https://api.yousign.app/v3"
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# Événements de cycle de vie d'une demande de signature (documentation Yousign).
SIGNATURE_EVENTS = (
    "signature_request.activated",
    "signature_request.done",
    "signature_request.expired",
    "signature_request.declined",
    "signature_request.canceled",
)


class YousignError(Exception):
    """Erreur d'appel à l'API Yousign."""


class YousignNotConfigured(YousignError):
    """Clé API absente — la voie électronique est indisponible."""


def is_configured() -> bool:
    """La signature électronique est-elle activée sur cette instance ?"""
    return bool(getattr(settings, "yousign_api_key", None))


def webhook_configured() -> bool:
    """Le secret de webhook est-il provisionné ?"""
    return bool(getattr(settings, "yousign_webhook_secret", None))


def _headers() -> dict[str, str]:
    if not is_configured():
        raise YousignNotConfigured("YOUSIGN_API_KEY manquant.")
    # Le jeton passe en en-tête d'autorisation, jamais en paramètre d'URL : une
    # URL complète est journalisée par tout intermédiaire.
    return {"Authorization": f"Bearer {settings.yousign_api_key}"}


def verify_webhook_signature(payload: bytes, signature_header: str | None) -> bool:
    """Vérifie l'empreinte HMAC-SHA256 du corps brut d'un webhook.

    Comparaison à **temps constant** : comparer deux empreintes avec ``==``
    laisse fuir, par le temps de réponse, le nombre d'octets corrects — de quoi
    reconstituer une signature valide octet par octet.

    L'en-tête peut être préfixé (``sha256=…``) selon la configuration ; les deux
    formes sont acceptées.
    """
    if not webhook_configured():
        raise YousignNotConfigured("YOUSIGN_WEBHOOK_SECRET manquant.")
    if not signature_header:
        return False
    received = signature_header.strip()
    if "=" in received:
        received = received.split("=", 1)[1].strip()
    expected = hmac.new(
        (settings.yousign_webhook_secret or "").encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received)


async def _request(method: str, path: str, **kwargs: Any) -> dict:
    url = f"{API_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(method, url, headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:  # réseau, DNS, timeout
        raise YousignError(f"Yousign injoignable : {exc}") from exc
    # On ne journalise jamais l'URL complète ni les en-têtes (jeton).
    logger.info("yousign %s %s -> %s", method, path, response.status_code)
    if response.status_code >= 400:
        raise YousignError(f"Yousign a répondu {response.status_code}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise YousignError("Réponse Yousign illisible") from exc


async def create_signature_request(
    *,
    name: str,
    document: bytes,
    filename: str,
    signer_email: str,
    signer_first_name: str,
    signer_last_name: str,
) -> dict:
    """Crée et active une demande de signature pour la booking note.

    Trois appels, dans l'ordre imposé par l'API : création de la demande, dépôt
    du document, ajout du signataire, puis activation. L'activation est ce qui
    déclenche l'envoi au signataire — tant qu'elle n'a pas eu lieu, rien ne part.
    """
    if not is_configured():
        raise YousignNotConfigured("YOUSIGN_API_KEY manquant.")

    created = await _request(
        "POST",
        "/signature_requests",
        json={"name": name, "delivery_mode": "email", "timezone": "Europe/Paris"},
    )
    request_id = created.get("id")
    if not request_id:
        raise YousignError("Yousign n'a pas renvoyé d'identifiant de demande.")

    document_response = await _request(
        "POST",
        f"/signature_requests/{request_id}/documents",
        files={"file": (filename, document)},
        data={"nature": "signable_document"},
    )
    document_id = document_response.get("id")
    if not document_id:
        raise YousignError("Yousign n'a pas renvoyé d'identifiant de document.")

    await _request(
        "POST",
        f"/signature_requests/{request_id}/signers",
        json={
            "info": {
                "first_name": signer_first_name,
                "last_name": signer_last_name,
                "email": signer_email,
                "locale": "fr",
            },
            # Niveau avancé : identification du signataire + scellement.
            "signature_level": "advanced_electronic_signature",
            "signature_authentication_mode": "otp_email",
        },
    )
    activated = await _request("POST", f"/signature_requests/{request_id}/activate")
    return {"id": request_id, "status": activated.get("status") or "activated"}


async def retrieve_signature_request(request_id: str) -> dict:
    """Relit l'état d'une demande **côté Yousign** — c'est elle qui fait foi."""
    if not is_configured():
        raise YousignNotConfigured("YOUSIGN_API_KEY manquant.")
    return await _request("GET", f"/signature_requests/{request_id}")


async def download_signed_document(request_id: str) -> bytes:
    """Télécharge le document signé (PDF scellé) d'une demande terminée."""
    if not is_configured():
        raise YousignNotConfigured("YOUSIGN_API_KEY manquant.")
    url = f"{API_BASE}/signature_requests/{request_id}/documents/download"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_headers())
    except httpx.HTTPError as exc:
        raise YousignError(f"Yousign injoignable : {exc}") from exc
    if response.status_code >= 400:
        raise YousignError(f"Téléchargement Yousign refusé ({response.status_code})")
    return response.content


def split_name(full_name: str | None, fallback_email: str | None = None) -> tuple[str, str]:
    """Découpe un nom en (prénom, nom) — Yousign exige les deux séparément.

    Sans nom exploitable, on retombe sur la partie locale de l'e-mail plutôt que
    d'envoyer une chaîne vide, que l'API rejette.
    """
    clean = (full_name or "").strip()
    if not clean:
        local = (fallback_email or "").split("@", 1)[0].strip()
        return (local or "Contact", "—")
    parts = clean.split()
    if len(parts) == 1:
        return (parts[0], "—")
    return (" ".join(parts[:-1]), parts[-1])
