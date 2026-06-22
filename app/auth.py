"""Authentication primitives — staff and client.

Two independent contexts share the same hashing + signing primitives:
- staff users (`users` table, cookie `towt_session`)
- client accounts (`client_accounts` table, cookie `towt_client_session`)

Each context has its own dependency (`get_current_staff`, `get_current_client`).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

STAFF_COOKIE = "towt_session"
CLIENT_COOKIE = "towt_client_session"
# Cookie intermédiaire pose au moment du POST /me/login quand l'utilisateur
# a MFA activé : courte durée (5 min), redirigé vers /me/login/mfa.
CLIENT_MFA_PENDING_COOKIE = "towt_client_mfa_pending"
CLIENT_MFA_PENDING_TTL_SECONDS = 5 * 60
STAFF_MFA_PENDING_COOKIE = "towt_staff_mfa_pending"
STAFF_MFA_PENDING_TTL_SECONDS = 5 * 60

# Cookie « appareil de confiance MFA » : posé après une validation MFA réussie,
# il permet de sauter le challenge MFA pendant 24 h sur ce navigateur.
STAFF_MFA_TRUSTED_COOKIE = "towt_staff_mfa_trusted"
CLIENT_MFA_TRUSTED_COOKIE = "towt_client_mfa_trusted"
MFA_TRUSTED_TTL_SECONDS = 24 * 60 * 60

# Durée de session staff par rôle (en minutes). Les marins / commandants
# embarquent pour 15+ jours sans satcom fiable → leur fenêtre par défaut
# (480 min = 8h) est trop courte. On donne 14 jours pour ces rôles, en
# gardant 8h pour les rôles bureau (qui doivent être ré-auth régulièrement).
STAFF_SESSION_MINUTES_BY_ROLE: dict[str, int] = {
    "marins": 14 * 24 * 60,
    "manager_maritime": 14 * 24 * 60,
}


def _session_minutes_for(role: str | None) -> int:
    if role and role in STAFF_SESSION_MINUTES_BY_ROLE:
        return STAFF_SESSION_MINUTES_BY_ROLE[role]
    return settings.access_token_expire_minutes


# Pour le décodage, on autorise jusqu'à la valeur max possible (sinon le
# cookie de 14j serait rejeté car serializer.loads valide max_age en dur).
_MAX_STAFF_SESSION_MINUTES = max(
    settings.access_token_expire_minutes,
    *STAFF_SESSION_MINUTES_BY_ROLE.values(),
)


_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_staff_serializer = URLSafeTimedSerializer(settings.secret_key, salt="staff-session")
_client_serializer = URLSafeTimedSerializer(settings.secret_key, salt="client-session")
_client_mfa_serializer = URLSafeTimedSerializer(settings.secret_key, salt="client-mfa-pending")
_staff_mfa_serializer = URLSafeTimedSerializer(settings.secret_key, salt="staff-mfa-pending")
_staff_mfa_trusted_serializer = URLSafeTimedSerializer(
    settings.secret_key, salt="staff-mfa-trusted"
)
_client_mfa_trusted_serializer = URLSafeTimedSerializer(
    settings.secret_key, salt="client-mfa-trusted"
)


# ---------------------------------------------------------------------------
# Hashing & secrets
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _pwd_context.verify(password, hashed)


def random_secret(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


# ---------------------------------------------------------------------------
# Session tokens (signed, time-limited)
# ---------------------------------------------------------------------------


def create_staff_session(user_id: int) -> str:
    payload = {"uid": user_id, "iat": datetime.now(UTC).timestamp()}
    return _staff_serializer.dumps(payload)


def create_client_session(client_id: int) -> str:
    payload = {"cid": client_id, "iat": datetime.now(UTC).timestamp()}
    return _client_serializer.dumps(payload)


def create_client_mfa_pending(client_id: int) -> str:
    """Token court (5min) signé pour la phase challenge MFA d'un login client."""
    payload = {"cid": client_id, "iat": datetime.now(UTC).timestamp()}
    return _client_mfa_serializer.dumps(payload)


def decode_client_mfa_pending(token: str) -> int | None:
    """Renvoie le client_id si le token est valide & non-expiré, sinon None."""
    if not token:
        return None
    try:
        payload = _client_mfa_serializer.loads(
            token,
            max_age=CLIENT_MFA_PENDING_TTL_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return None
    return payload.get("cid")


def cookie_kwargs_for_client_mfa_pending(request: Request | None = None) -> dict:
    return {
        "key": CLIENT_MFA_PENDING_COOKIE,
        "max_age": CLIENT_MFA_PENDING_TTL_SECONDS,
        "httponly": True,
        "secure": _is_https(request),
        "samesite": "lax",
        "path": "/",
    }


def create_staff_mfa_pending(user_id: int) -> str:
    """Token court (5min) signé pour la phase challenge MFA d'un login staff."""
    payload = {"uid": user_id, "iat": datetime.now(UTC).timestamp()}
    return _staff_mfa_serializer.dumps(payload)


def decode_staff_mfa_pending(token: str) -> int | None:
    if not token:
        return None
    try:
        payload = _staff_mfa_serializer.loads(
            token,
            max_age=STAFF_MFA_PENDING_TTL_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return None
    return payload.get("uid")


def cookie_kwargs_for_staff_mfa_pending(request: Request | None = None) -> dict:
    return {
        "key": STAFF_MFA_PENDING_COOKIE,
        "max_age": STAFF_MFA_PENDING_TTL_SECONDS,
        "httponly": True,
        "secure": _is_https(request),
        "samesite": "lax",
        "path": "/",
    }


# ── Appareil de confiance MFA (persistance 24 h) ────────────────────────────


def create_staff_mfa_trusted(user_id: int) -> str:
    """Token signé (24 h) attestant d'un MFA staff réussi sur cet appareil."""
    payload = {"uid": user_id, "iat": datetime.now(UTC).timestamp()}
    return _staff_mfa_trusted_serializer.dumps(payload)


def decode_staff_mfa_trusted(token: str, user_id: int) -> bool:
    """True si le token de confiance est valide, non-expiré et lié à ``user_id``."""
    if not token:
        return False
    try:
        payload = _staff_mfa_trusted_serializer.loads(token, max_age=MFA_TRUSTED_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return payload.get("uid") == user_id


def create_client_mfa_trusted(client_id: int) -> str:
    """Token signé (24 h) attestant d'un MFA client réussi sur cet appareil."""
    payload = {"cid": client_id, "iat": datetime.now(UTC).timestamp()}
    return _client_mfa_trusted_serializer.dumps(payload)


def decode_client_mfa_trusted(token: str, client_id: int) -> bool:
    if not token:
        return False
    try:
        payload = _client_mfa_trusted_serializer.loads(token, max_age=MFA_TRUSTED_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return payload.get("cid") == client_id


def cookie_kwargs_for_mfa_trusted(key: str, request: Request | None = None) -> dict:
    """Kwargs ``set_cookie`` d'un cookie « appareil de confiance MFA » (24 h)."""
    return {
        "key": key,
        "max_age": MFA_TRUSTED_TTL_SECONDS,
        "httponly": True,
        "secure": _is_https(request),
        "samesite": "lax",
        "path": "/",
    }


def _decode(token: str, serializer: URLSafeTimedSerializer, max_age: int) -> dict:
    try:
        return serializer.loads(token, max_age=max_age)
    except SignatureExpired as e:
        raise AuthExpired() from e
    except BadSignature as e:
        raise AuthInvalid() from e


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base authentication error."""


class AuthRequired(AuthError):
    """No session — redirect to login."""


class AuthInvalid(AuthError):
    """Tampered or unparseable token."""


class AuthExpired(AuthError):
    """Session expired."""


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def get_current_staff(
    session_cookie: Annotated[str | None, Cookie(alias=STAFF_COOKIE)] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated staff User, or raise AuthRequired.

    Décode le cookie avec la durée *maximale* admise (14j marins), puis
    applique en post-check la limite par rôle de l'utilisateur. Si le
    cookie a été émis trop tôt pour son rôle actuel (ex. rôle dégradé
    après émission), on lève AuthExpired.
    """
    from app.models.user import User  # local import to avoid cycles

    if not session_cookie:
        raise AuthRequired()
    payload = _decode(
        session_cookie,
        _staff_serializer,
        max_age=_MAX_STAFF_SESSION_MINUTES * 60,
    )
    user_id = payload.get("uid")
    if not user_id:
        raise AuthInvalid()
    user = (
        await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if not user:
        raise AuthInvalid()
    # Vérif fenêtre par rôle (post-decode)
    iat = payload.get("iat")
    if iat:
        age_s = datetime.now(UTC).timestamp() - float(iat)
        if age_s > _session_minutes_for(user.role) * 60:
            raise AuthExpired()
    return user


async def get_current_client(
    session_cookie: Annotated[str | None, Cookie(alias=CLIENT_COOKIE)] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated ClientAccount, or raise AuthRequired."""
    from app.models.client_account import ClientAccount  # local

    if not session_cookie:
        raise AuthRequired()
    payload = _decode(
        session_cookie,
        _client_serializer,
        max_age=settings.client_session_days * 86400,
    )
    client_id = payload.get("cid")
    if not client_id:
        raise AuthInvalid()
    client = (
        await db.execute(
            select(ClientAccount).where(
                ClientAccount.id == client_id, ClientAccount.is_verified.is_(True)
            )
        )
    ).scalar_one_or_none()
    if not client:
        raise AuthInvalid()
    return client


async def get_optional_staff(
    session_cookie: Annotated[str | None, Cookie(alias=STAFF_COOKIE)] = None,
    db: AsyncSession = Depends(get_db),
):
    """Like get_current_staff but returns None instead of raising."""
    if not session_cookie:
        return None
    try:
        return await get_current_staff(session_cookie=session_cookie, db=db)
    except AuthError:
        return None


async def get_optional_client(
    session_cookie: Annotated[str | None, Cookie(alias=CLIENT_COOKIE)] = None,
    db: AsyncSession = Depends(get_db),
):
    """Like get_current_client but returns None instead of raising."""
    if not session_cookie:
        return None
    try:
        return await get_current_client(session_cookie=session_cookie, db=db)
    except AuthError:
        return None


def _is_https(request: Request | None) -> bool:
    """Return True iff the effective scheme is HTTPS.

    Honors X-Forwarded-Proto when set by the reverse proxy (nginx).
    Falls back to request.url.scheme. Returns False if no request given,
    which leaves the cookie usable over plain HTTP during bootstrap.
    """
    if request is None:
        return False
    forwarded = request.headers.get("x-forwarded-proto", "").lower()
    if forwarded:
        return forwarded == "https"
    return request.url.scheme == "https"


def cookie_kwargs_for_staff(
    request: Request | None = None,
    *,
    role: str | None = None,
) -> dict:
    """Cookie staff — ``max_age`` ajusté selon le rôle (14j pour marins).

    Si ``role`` n'est pas fourni on prend la durée par défaut (8h)
    — applicable au moment du login où on peut passer ``role=user.role``.
    """
    return {
        "key": STAFF_COOKIE,
        "max_age": _session_minutes_for(role) * 60,
        "httponly": True,
        "secure": _is_https(request),
        "samesite": "lax",
        "path": "/",
    }


def cookie_kwargs_for_client(request: Request | None = None) -> dict:
    return {
        "key": CLIENT_COOKIE,
        "max_age": settings.client_session_days * 86400,
        "httponly": True,
        "secure": _is_https(request),
        "samesite": "lax",
        "path": "/",
    }
