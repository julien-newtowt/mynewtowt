"""WebAuthn / FIDO2 helpers — registration + authentication.

Encapsule la lib ``webauthn`` (Duo Labs) avec notre modèle ``WebAuthnCredential``.

Concepts :
- **Registration** : un user déjà loggué demande à ajouter une passkey.
  Le serveur génère un challenge + options ; le navigateur appelle
  ``navigator.credentials.create({publicKey: options})`` ; le résultat
  (attestation) est posté au serveur qui vérifie et stocke (credential_id,
  public_key, sign_count, transports).
- **Authentication** : à la connexion, le serveur génère un challenge ;
  ``navigator.credentials.get({publicKey: options})`` produit une
  assertion ; le serveur vérifie la signature avec la clé publique
  stockée, incrémente le sign_count, et autorise la session.

Challenges :
- Stockés dans un cookie courte durée (5 min, signé itsdangerous,
  ``salt="webauthn-challenge"``) pour ne PAS dépendre d'un store
  Redis/session. Anti-replay : un challenge consommé n'est plus valide
  (cookie supprimé après verify).

User handle :
- WebAuthn impose un identifiant binaire stable pour le user. On utilise
  ``f"{owner_type}:{owner_id}".encode()`` — stable, lisible côté logs.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Literal

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.webauthn_credential import WebAuthnCredential


CHALLENGE_TTL_SECONDS = 5 * 60
CHALLENGE_COOKIE_REG = "towt_webauthn_reg_challenge"
CHALLENGE_COOKIE_AUTH = "towt_webauthn_auth_challenge"

_challenge_serializer = URLSafeTimedSerializer(settings.secret_key, salt="webauthn-challenge")


# ─────────────────────────────────────────────────────────────────────
# Encoding helpers — la spec WebAuthn utilise base64url SANS padding
# ─────────────────────────────────────────────────────────────────────


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


# ─────────────────────────────────────────────────────────────────────
# Challenge state (cookie signé)
# ─────────────────────────────────────────────────────────────────────


def sign_challenge(challenge: bytes, *, owner_type: str, owner_id: int | None) -> str:
    payload = {
        "ch": b64url_encode(challenge),
        "ot": owner_type,
        "oi": owner_id,
        "iat": datetime.now(timezone.utc).timestamp(),
    }
    return _challenge_serializer.dumps(payload)


def read_challenge(token: str) -> dict | None:
    """Renvoie le payload (ou None si invalide/expiré)."""
    if not token:
        return None
    try:
        return _challenge_serializer.loads(token, max_age=CHALLENGE_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def cookie_kwargs_for_challenge(name: str, *, secure: bool) -> dict:
    return {
        "key": name,
        "max_age": CHALLENGE_TTL_SECONDS,
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
    }


# ─────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────


def make_user_handle(owner_type: str, owner_id: int) -> bytes:
    """Identifiant binaire WebAuthn pour ce user (stable)."""
    return f"{owner_type}:{owner_id}".encode("utf-8")


async def begin_registration(
    db: AsyncSession,
    *,
    owner_type: Literal["client", "staff"],
    owner_id: int,
    user_name: str,
    user_display_name: str,
) -> tuple[str, bytes]:
    """Génère les options de création + renvoie (json_options, challenge).

    Le caller doit poser un cookie ``CHALLENGE_COOKIE_REG`` contenant
    ``sign_challenge(challenge, owner_type=..., owner_id=...)`` avant
    de répondre au browser.
    """
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    # Exclude credentials déjà enregistrés (évite les doublons même device)
    existing = list((await db.execute(
        select(WebAuthnCredential)
        .where(WebAuthnCredential.owner_type == owner_type)
        .where(WebAuthnCredential.owner_id == owner_id)
    )).scalars().all())
    exclude = [
        PublicKeyCredentialDescriptor(id=b64url_decode(c.credential_id))
        for c in existing
    ]

    opts = generate_registration_options(
        rp_id=settings.effective_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=make_user_handle(owner_type, owner_id),
        user_name=user_name,
        user_display_name=user_display_name,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        timeout=60_000,
    )
    return options_to_json(opts), bytes(opts.challenge)


async def complete_registration(
    db: AsyncSession,
    *,
    owner_type: str,
    owner_id: int,
    challenge: bytes,
    credential_json: str,
    expected_origin: str,
    name: str | None = None,
) -> WebAuthnCredential:
    """Vérifie l'attestation et persiste le credential.

    ``credential_json`` est la string brute envoyée par le navigateur
    (JSON.stringify du PublicKeyCredential). ``expected_origin`` doit
    matcher l'origine réelle de la requête (https://my.newtowt.eu).
    Raise ``ValueError`` sur échec (cause exposée au caller pour log).
    """
    from webauthn import verify_registration_response

    verification = verify_registration_response(
        credential=credential_json,
        expected_challenge=challenge,
        expected_origin=expected_origin,
        expected_rp_id=settings.effective_rp_id,
    )

    cred_id_b64 = b64url_encode(verification.credential_id)
    # Doublon ? (paranoid — exclude_credentials a déjà géré normalement)
    dup = (await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == cred_id_b64)
    )).scalar_one_or_none()
    if dup is not None:
        raise ValueError("Credential déjà enregistré sur ce compte ou un autre")

    # Extraction transports + aaguid depuis le JSON brut
    transports = None
    aaguid = None
    try:
        parsed = json.loads(credential_json)
        resp = parsed.get("response", {})
        t = resp.get("transports")
        if isinstance(t, list):
            transports = ",".join(t)[:80]
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    aaguid_bytes = getattr(verification, "aaguid", None)
    if aaguid_bytes:
        try:
            aaguid = str(aaguid_bytes)[:40]
        except Exception:
            pass

    cred = WebAuthnCredential(
        owner_type=owner_type,
        owner_id=owner_id,
        credential_id=cred_id_b64,
        public_key=bytes(verification.credential_public_key),
        sign_count=int(verification.sign_count),
        name=(name or "").strip()[:120] or None,
        transports=transports,
        aaguid=aaguid,
    )
    db.add(cred)
    await db.flush()
    return cred


# ─────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────


async def begin_authentication(
    db: AsyncSession,
    *,
    owner_type: Literal["client", "staff"] | None,
    owner_id: int | None,
) -> tuple[str, bytes]:
    """Génère les options de challenge pour login.

    Si ``owner_id`` est fourni, on restreint la liste des credentials à
    ceux du user (cas post-password : on sait qui essaie de se connecter).
    Sinon (passkey "passwordless"), on laisse le navigateur proposer
    n'importe quel credential discovrable (resident key) — le user_handle
    sera retourné dans l'assertion.
    """
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor, UserVerificationRequirement,
    )

    allow: list[PublicKeyCredentialDescriptor] = []
    if owner_type and owner_id:
        creds = list((await db.execute(
            select(WebAuthnCredential)
            .where(WebAuthnCredential.owner_type == owner_type)
            .where(WebAuthnCredential.owner_id == owner_id)
        )).scalars().all())
        allow = [
            PublicKeyCredentialDescriptor(id=b64url_decode(c.credential_id))
            for c in creds
        ]

    opts = generate_authentication_options(
        rp_id=settings.effective_rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
        timeout=60_000,
    )
    return options_to_json(opts), bytes(opts.challenge)


async def complete_authentication(
    db: AsyncSession,
    *,
    challenge: bytes,
    credential_json: str,
    expected_origin: str,
) -> WebAuthnCredential:
    """Vérifie l'assertion, incrémente sign_count, renvoie le credential matched.

    Raise ``ValueError`` si credential inconnu ou signature invalide.
    """
    from webauthn import verify_authentication_response

    # Lookup credential par son credential_id (envoyé en clair dans le JSON)
    try:
        parsed = json.loads(credential_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"credential JSON invalide: {e}")
    cred_id_raw = parsed.get("id") or parsed.get("rawId")
    if not cred_id_raw:
        raise ValueError("credential.id manquant")

    cred = (await db.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == cred_id_raw)
    )).scalar_one_or_none()
    if cred is None:
        raise ValueError("Credential inconnu")

    verification = verify_authentication_response(
        credential=credential_json,
        expected_challenge=challenge,
        expected_origin=expected_origin,
        expected_rp_id=settings.effective_rp_id,
        credential_public_key=cred.public_key,
        credential_current_sign_count=cred.sign_count,
    )

    # Anti-clone : nouveau sign_count doit être > stocké (ou 0 si l'authenticator
    # n'incrémente pas — verify_authentication_response l'autorise pour les
    # passkeys synchronisées multi-device).
    cred.sign_count = int(verification.new_sign_count)
    cred.last_used_at = datetime.now(timezone.utc)
    await db.flush()
    return cred
