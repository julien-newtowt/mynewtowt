"""Double-submit cookie CSRF protection.

The middleware:
- generates a token on every request (or reuses the existing cookie),
- exposes it on ``request.state.csrf_token`` so templates / HTMX clients
  can render it even on the very first visit (when the cookie is only
  set in the response),
- always re-sets the cookie on the response so it stays in sync,
- on mutating requests, requires either an ``x-csrf-token`` header or a
  ``_csrf`` form field whose value matches the cookie.

Body caching: for ``application/x-www-form-urlencoded`` requests we read
``request.body()`` once (which caches the bytes in Starlette's
``Request._body``), then parse only the CSRF field locally. Downstream
FastAPI ``Form(...)`` dependencies re-parse the cached body and see the
other fields normally.
"""
from __future__ import annotations

import secrets
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CSRF_COOKIE = "towt_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_FORM_FIELD = "_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PATHS_PREFIXES = (
    "/api/v1/",        # API expects its own auth (API key / bearer)
    "/webhooks/",      # external webhooks sign their payloads
    "/health",
    "/metrics",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cookie_value = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        request.state.csrf_token = cookie_value

        if (
            request.method not in SAFE_METHODS
            and not any(request.url.path.startswith(p) for p in EXEMPT_PATHS_PREFIXES)
        ):
            header_token = request.headers.get(CSRF_HEADER)
            form_token: str | None = None
            content_type = request.headers.get("content-type", "")
            if not header_token and content_type.startswith(
                "application/x-www-form-urlencoded"
            ):
                # Cache body so downstream Form(...) parsing still works.
                body_bytes = await request.body()
                parsed = parse_qs(body_bytes.decode("utf-8", errors="replace"))
                values = parsed.get(CSRF_FORM_FIELD)
                form_token = values[0] if values else None

            submitted = header_token or form_token
            if not submitted or submitted != cookie_value:
                return Response(
                    "CSRF validation failed", status_code=403, media_type="text/plain"
                )

        response = await call_next(request)
        response.set_cookie(
            CSRF_COOKIE,
            cookie_value,
            httponly=False,  # JS must read it to set the header (HTMX injects)
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/",
        )
        return response
