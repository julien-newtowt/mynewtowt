"""Maintenance mode middleware.

If the marker file `/tmp/.maintenance` exists, all requests other than
`/health` and `/static/*` return a maintenance HTML page (status 503).

The marker is toggled by `scripts/deploy.sh` and `scripts/maintenance.sh`.
"""
from __future__ import annotations

from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse

MARKER = Path("/tmp/.maintenance")

EXEMPT_PREFIXES = ("/health", "/static/", "/.well-known/")

MAINTENANCE_HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Maintenance — NEWTOWT</title>
<style>
body{font-family:system-ui,sans-serif;background:#08121A;color:#F4F7F9;
display:grid;place-items:center;min-height:100vh;margin:0}
.box{background:#0F1E27;border:1px solid #28404E;border-radius:14px;
padding:48px;max-width:520px;text-align:center}
h1{color:#87BD29;margin:0 0 12px}
p{color:#B8C5CE}
</style></head><body>
<div class="box">
<h1>Maintenance en cours</h1>
<p>L'application est temporairement indisponible le temps d'un déploiement.</p>
<p>Réessayez dans quelques minutes.</p>
</div></body></html>
"""


class MaintenanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if MARKER.exists() and not any(
            request.url.path.startswith(p) for p in EXEMPT_PREFIXES
        ):
            return HTMLResponse(MAINTENANCE_HTML, status_code=503)
        return await call_next(request)
