"""HTTP security headers — applied to every response.

The CSP is restrictive: only Stripe + Mapbox + OSM Nominatim + fonts.gstatic
are allowed cross-origin. Inline scripts forbidden (HTMX uses event attrs,
not inline scripts). Inline styles allowed because of Mapbox runtime CSS.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com https://js.stripe.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob: "
    "https://*.tile.openstreetmap.org "
    "https://api.mapbox.com https://api.maptiler.com "
    "https://demotiles.maplibre.org; "
    "worker-src 'self' blob:; "
    "connect-src 'self' https://api.stripe.com "
    "https://api.mapbox.com https://api.maptiler.com "
    "https://demotiles.maplibre.org "
    "https://nominatim.openstreetmap.org; "
    "frame-src https://js.stripe.com https://checkout.stripe.com; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self' https://checkout.stripe.com; "
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
            "camera=(), microphone=(), geolocation=(), payment=(self)"
        )
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
        return response
