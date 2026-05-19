"""ForcePasswordChangeMiddleware.

Si l'utilisateur staff (cookie `towt_session`) a `must_change_password=True`,
toutes les requêtes sont redirigées vers `/admin/my-account/change-password`
sauf cette page elle-même, `/logout`, et les ressources statiques.

Posée APRÈS le CSRF middleware pour bénéficier d'une session déjà décodée
si possible (ici on relit le cookie pour rester indépendant).
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

EXEMPT_PREFIXES = (
    "/admin/my-account/change-password",
    "/logout",
    "/static/",
    "/health",
    "/.well-known/",
    "/login",
    "/api/v1/health",
)


class ForcePasswordChangeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        # Only enforce on browser HTML routes — leave APIs untouched
        accept = request.headers.get("accept", "")
        if "text/html" not in accept and "application/xhtml+xml" not in accept:
            return await call_next(request)

        token = request.cookies.get("towt_session")
        if not token:
            return await call_next(request)

        # Decode the session token to find the user_id. Done locally (not via
        # the auth.get_current_staff dependency) to avoid coupling and DB lookup
        # cost on every request. The full check (DB read) happens in the dest.
        try:
            from app.auth import _staff_serializer
            from app.config import settings
            max_age = settings.access_token_expire_minutes * 60
            payload = _staff_serializer.loads(token, max_age=max_age)
            user_id = payload.get("uid") if isinstance(payload, dict) else None
        except Exception:
            return await call_next(request)

        if not user_id:
            return await call_next(request)

        # Check the user's must_change_password flag
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
