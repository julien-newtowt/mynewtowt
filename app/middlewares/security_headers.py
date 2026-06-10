"""HTTP security headers — applied to every response.

CSP restrictive : seuls Mapbox / MapTiler / OSM Nominatim et les fonts
Google sont autorisés cross-origin. Pas de scripts inline (HTMX utilise
des attributs d'événement). Styles inline tolérés (CSS runtime Mapbox).

V3.1 : Stripe retiré — NEWTOWT ne traite plus de paiement dans l'app
(facturation par virement bancaire post-confirmation commerciale).
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob: "
    "https://*.tile.openstreetmap.org "
    "https://api.mapbox.com https://api.maptiler.com "
    "https://demotiles.maplibre.org; "
    "worker-src 'self' blob:; "
    "connect-src 'self' "
    "https://api.mapbox.com https://api.maptiler.com "
    "https://demotiles.maplibre.org "
    "https://nominatim.openstreetmap.org; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
        return response
