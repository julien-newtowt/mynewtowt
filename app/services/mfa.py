"""TOTP MFA (RFC 6238) — setup, verify, QR code rendering, recovery codes.

Pile : ``pyotp`` pour TOTP, ``segno`` pour QR (pure Python, pas de Pillow).
Les imports sont locaux pour ne pas planter le boot si une dépendance
n'est pas installée — la route appelante affiche alors un message clair.

Modèle de données :
- ``ClientAccount.mfa_secret`` / ``mfa_enabled`` pour le client.
- ``User.mfa_secret`` / ``mfa_enabled`` pour le staff.
- ``mfa_recovery_codes`` (polymorphe owner_type/id) pour les 10 codes
  de récupération hashés SHA-256.

Le secret TOTP est posé dès la phase *setup* mais ``mfa_enabled`` ne
passe à True qu'après la 1re vérification réussie (anti-lock-out).
"""
from __future__ import annotations

import hashlib
import secrets
from base64 import b64encode
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mfa_recovery_code import MfaRecoveryCode


def generate_secret() -> str:
    """Renvoie un nouveau secret base32 pour TOTP."""
    import pyotp
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_email: str, *, issuer: str = "NEWTOWT") -> str:
    """URL otpauth:// pour scan par Google Authenticator / Authy / 1Password / ..."""
    import pyotp
    return pyotp.totp.TOTP(secret).provisioning_uri(account_email, issuer_name=issuer)


def verify_totp(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Vérifie un code TOTP 6 chiffres.

    ``valid_window=1`` tolère une dérive d'horloge de ±30s (utile sur
    téléphones mal synchronisés).
    """
    if not secret or not code:
        return False
    import pyotp
    try:
        return pyotp.totp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=valid_window)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
# Recovery codes
# ─────────────────────────────────────────────────────────────────────


RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 6  # 12 chars hex → ~48 bits entropie, suffisant pour un usage 1-shot


def _hash_code(code: str) -> str:
    """SHA-256 hex du code normalisé (lowercase, sans tirets/espaces)."""
    norm = code.strip().lower().replace("-", "").replace(" ", "")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _format_code(raw_hex: str) -> str:
    """``abcdef123456`` → ``abcd-ef12-3456`` (plus lisible à recopier)."""
    return f"{raw_hex[:4]}-{raw_hex[4:8]}-{raw_hex[8:12]}"


async def generate_recovery_codes(
    db: AsyncSession, *, owner_type: str, owner_id: int,
) -> list[str]:
    """Régénère 10 codes : purge les anciens, insère les nouveaux.

    Renvoie la liste des codes en clair (formatés xxxx-xxxx-xxxx) — à
    afficher UNE SEULE fois au user. Les hashes sont persistés.
    """
    # Purge anciens codes
    from sqlalchemy import delete
    await db.execute(
        delete(MfaRecoveryCode)
        .where(MfaRecoveryCode.owner_type == owner_type)
        .where(MfaRecoveryCode.owner_id == owner_id)
    )
    plain: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(RECOVERY_CODE_BYTES)
        code = _format_code(raw)
        plain.append(code)
        db.add(MfaRecoveryCode(
            owner_type=owner_type, owner_id=owner_id,
            code_hash=_hash_code(code),
        ))
    await db.flush()
    return plain


async def consume_recovery_code(
    db: AsyncSession, *, owner_type: str, owner_id: int, code: str,
) -> bool:
    """Tente de consommer un code de récupération. Renvoie True si valide.

    Le code est marqué ``used_at = now()`` (jamais ré-utilisable) et la
    fonction est constant-time-ish : on calcule toujours le hash même
    si aucun code ne match.
    """
    if not code:
        return False
    h = _hash_code(code)
    stmt = (
        select(MfaRecoveryCode)
        .where(MfaRecoveryCode.owner_type == owner_type)
        .where(MfaRecoveryCode.owner_id == owner_id)
        .where(MfaRecoveryCode.code_hash == h)
        .where(MfaRecoveryCode.used_at.is_(None))
        .limit(1)
    )
    rc = (await db.execute(stmt)).scalar_one_or_none()
    if rc is None:
        return False
    rc.used_at = datetime.now(timezone.utc)
    await db.flush()
    return True


async def count_unused_recovery_codes(
    db: AsyncSession, *, owner_type: str, owner_id: int,
) -> int:
    from sqlalchemy import func as sqlfunc
    stmt = (
        select(sqlfunc.count(MfaRecoveryCode.id))
        .where(MfaRecoveryCode.owner_type == owner_type)
        .where(MfaRecoveryCode.owner_id == owner_id)
        .where(MfaRecoveryCode.used_at.is_(None))
    )
    return int((await db.scalar(stmt)) or 0)


# ─────────────────────────────────────────────────────────────────────
# QR rendering
# ─────────────────────────────────────────────────────────────────────


def qr_data_uri(otpauth_uri: str) -> str | None:
    """Renvoie un ``data:image/svg+xml;base64,...`` pour ``<img src=...>``.

    Inline = pas d'appel réseau, compatible CSP (data: explicitement
    autorisé par img-src). Renvoie None si segno indisponible — la route
    affichera alors juste le secret en clair pour saisie manuelle.
    """
    try:
        import segno
    except ImportError:
        return None
    try:
        qr = segno.make(otpauth_uri, error="M")
        buf = BytesIO()
        qr.save(buf, kind="svg", scale=4, border=2, dark="#0D5966", light="#FFFFFF")
        return "data:image/svg+xml;base64," + b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None
