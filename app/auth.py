"""Authentication primitives — staff and client.

Two independent contexts share the same hashing + signing primitives:
- staff users (`users` table, cookie `towt_session`)
- client accounts (`client_accounts` table, cookie `towt_client_session`)

Each context has its own dependency (`get_current_staff`, `get_current_client`).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

STAFF_COOKIE = "towt_session"
CLIENT_COOKIE = "towt_client_session"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_staff_serializer = URLSafeTimedSerializer(settings.secret_key, salt="staff-session")
_client_serializer = URLSafeTimedSerializer(settings.secret_key, salt="client-session")


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
    payload = {"uid": user_id, "iat": datetime.now(timezone.utc).timestamp()}
    return _staff_serializer.dumps(payload)


def create_client_session(client_id: int) -> str:
    payload = {"cid": client_id, "iat": datetime.now(timezone.utc).timestamp()}
    return _client_serializer.dumps(payload)


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
    """Return the authenticated staff User, or raise AuthRequired."""
    from app.models.user import User  # local import to avoid cycles

    if not session_cookie:
        raise AuthRequired()
    payload = _decode(
        session_cookie,
        _staff_serializer,
        max_age=settings.access_token_expire_minutes * 60,
    )
    user_id = payload.get("uid")
    if not user_id:
        raise AuthInvalid()
    user = (
        await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if not user:
        raise AuthInvalid()
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


def _is_https(request: "Request | None") -> bool:
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


def cookie_kwargs_for_staff(request: "Request | None" = None) -> dict:
    return {
        "key": STAFF_COOKIE,
        "max_age": settings.access_token_expire_minutes * 60,
        "httponly": True,
        "secure": _is_https(request),
        "samesite": "lax",
        "path": "/",
    }


def cookie_kwargs_for_client(request: "Request | None" = None) -> dict:
    return {
        "key": CLIENT_COOKIE,
        "max_age": settings.client_session_days * 86400,
        "httponly": True,
        "secure": _is_https(request),
        "samesite": "lax",
        "path": "/",
    }
