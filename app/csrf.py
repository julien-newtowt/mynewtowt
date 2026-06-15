"""Double-submit cookie CSRF protection.

The middleware:
- generates a token on every request (or reuses the existing cookie),
- exposes it on ``request.state.csrf_token`` so templates / HTMX clients
  can render it even on the very first visit (when the cookie is only
  set in the response),
- (re)sets the cookie on the response **only for HTML documents, or when the
  client already had the cookie**. This avoids a double-submit race on the
  very first visit: a brand-new visitor (no cookie) fetches the page *and*
  eager sub-resources (``/favicon.ico``, ``/static/*``, ``/manifest.json``…)
  in parallel; each cookie-less request would otherwise mint a *different*
  token and the last response to land would win the cookie — desyncing it
  from the ``_csrf`` token baked into the form → "CSRF validation failed" on
  first login. Scoping the Set-Cookie to HTML documents keeps the cookie in
  sync with the rendered form.
- on mutating requests, requires either an ``x-csrf-token`` header or a
  ``_csrf`` form field whose value matches the cookie.

Body caching: for ``application/x-www-form-urlencoded`` requests we read
``request.body()`` once (which caches the bytes in Starlette's
``Request._body``), then parse only the CSRF field locally. Downstream
FastAPI ``Form(...)`` dependencies re-parse the cached body and see the
other fields normally.
"""

from __future__ import annotations

import re
import secrets
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CSRF_COOKIE = "towt_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_FORM_FIELD = "_csrf"


def _extract_multipart_field(body: bytes, field: str) -> str | None:
    """Extrait la valeur d'un champ texte d'un corps multipart, sans
    consommer le parser (on lit ``request.body()`` mis en cache, le parsing
    fichier en aval reste possible)."""
    pattern = rb'name="' + re.escape(field.encode()) + rb'"\r\n\r\n(.*?)\r\n--'
    m = re.search(pattern, body, re.DOTALL)
    return m.group(1).decode("utf-8", errors="replace") if m else None


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PATHS_PREFIXES = (
    "/api/v1/",  # API expects its own auth (API key / bearer)
    "/api/tracking/",  # Power Automate satcom ingest — auth via X-API-Token
    "/webhooks/",  # external webhooks sign their payloads
    "/api/veille/",  # Power Automate veille refresh — auth via X-API-Token
    "/health",
    "/metrics",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cookie_value = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        request.state.csrf_token = cookie_value

        if request.method not in SAFE_METHODS and not any(
            request.url.path.startswith(p) for p in EXEMPT_PATHS_PREFIXES
        ):
            header_token = request.headers.get(CSRF_HEADER)
            form_token: str | None = None
            content_type = request.headers.get("content-type", "")
            if not header_token and content_type.startswith("application/x-www-form-urlencoded"):
                # Cache body so downstream Form(...) parsing still works.
                body_bytes = await request.body()
                parsed = parse_qs(body_bytes.decode("utf-8", errors="replace"))
                values = parsed.get(CSRF_FORM_FIELD)
                form_token = values[0] if values else None
            elif not header_token and content_type.startswith("multipart/form-data"):
                # File-upload forms : on met en cache le corps via body() (rejoué
                # en aval par BaseHTTPMiddleware) puis on extrait juste _csrf des
                # octets bruts — le parsing fichier FastAPI en aval reste intact.
                body_bytes = await request.body()
                form_token = _extract_multipart_field(body_bytes, CSRF_FORM_FIELD)

            submitted = header_token or form_token
            if not submitted or submitted != cookie_value:
                return Response("CSRF validation failed", status_code=403, media_type="text/plain")

        response = await call_next(request)
        # On ne (re)pose le cookie que sur un document HTML, ou si le client
        # l'avait déjà : sinon les requêtes parallèles cookie-less du tout
        # premier hit (favicon, /static, manifest…) forgeraient chacune un
        # token différent et écraseraient celui aligné sur le formulaire.
        had_cookie = request.cookies.get(CSRF_COOKIE) is not None
        is_html = response.headers.get("content-type", "").startswith("text/html")
        if had_cookie or is_html:
            response.set_cookie(
                CSRF_COOKIE,
                cookie_value,
                httponly=False,  # JS must read it to set the header (HTMX injects)
                secure=request.url.scheme == "https",
                samesite="lax",
                path="/",
            )
        return response
