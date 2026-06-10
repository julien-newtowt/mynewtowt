"""ForcePasswordChangeMiddleware + ForceMfaForAdminMiddleware.

Deux enforcements montés à la suite (ordre d'inscription ≠ ordre
d'exécution Starlette) :

1. ``must_change_password=True`` → redirect /admin/my-account/change-password.
2. Si ``settings.require_mfa_for_admin=True`` et user.role="administrateur"
   et !user.mfa_enabled → redirect /admin/my-account/mfa.

Pages exemptées : la cible elle-même, /logout, /static/*, /login.
Posées APRÈS le CSRF middleware pour bénéficier d'un cookie déjà géré.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse


EXEMPT_PREFIXES_PWD = (
    "/admin/my-account/change-password",
    "/logout",
    "/static/",
    "/health",
    "/.well-known/",
    "/login",
    "/api/v1/health",
)

EXEMPT_PREFIXES_MFA = (
    "/admin/my-account",          # MFA setup + verify + change-password
    "/logout",
    "/static/",
    "/health",
    "/.well-known/",
    "/login",
    "/api/v1/health",
)


async def _decode_staff_user_id(request: Request) -> int | None:
    """Renvoie l'user_id du staff loggué, ou None si pas de session.

    Décodage local (sans dépendance) pour rester léger sur chaque requête.
    Le full-check (is_active, DB read) reste à la charge du destinataire.
    """
    token = request.cookies.get("towt_session")
    if not token:
        return None
    try:
        from app.auth import _staff_serializer, _MAX_STAFF_SESSION_MINUTES
        payload = _staff_serializer.loads(
            token, max_age=_MAX_STAFF_SESSION_MINUTES * 60,
        )
        return payload.get("uid") if isinstance(payload, dict) else None
    except Exception:
        return None


def _is_html_request(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "application/xhtml+xml" in accept


class ForcePasswordChangeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES_PWD):
            return await call_next(request)
        if not _is_html_request(request):
            return await call_next(request)

        user_id = await _decode_staff_user_id(request)
        if not user_id:
            return await call_next(request)

        from app.database import SessionLocal
        from app.models.user import User
        async with SessionLocal() as db:
            user = await db.get(User, user_id)
            if user and user.must_change_password:
                return RedirectResponse(
                    url="/admin/my-account/change-password",
                    status_code=303,
                )
        return await call_next(request)


class ForceMfaForAdminMiddleware(BaseHTTPMiddleware):
    """Force l'activation MFA pour le rôle administrateur.

    Si ``settings.require_mfa_for_admin`` est False, le middleware est
    no-op. Si True, tout admin sans MFA est redirigé vers
    /admin/my-account/mfa sur la 1re requête HTML jusqu'à activation.
    """

    async def dispatch(self, request: Request, call_next):
        from app.config import settings
        if not settings.require_mfa_for_admin:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES_MFA):
            return await call_next(request)
        if not _is_html_request(request):
            return await call_next(request)

        user_id = await _decode_staff_user_id(request)
        if not user_id:
            return await call_next(request)

        from app.database import SessionLocal
        from app.models.user import User
        async with SessionLocal() as db:
            user = await db.get(User, user_id)
            if user and user.role == "administrateur" and not user.mfa_enabled:
                return RedirectResponse(
                    url="/admin/my-account/mfa", status_code=303,
                )
        return await call_next(request)
