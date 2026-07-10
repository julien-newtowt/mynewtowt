"""HTTP security headers — applied to every response.

CSP restrictive : seuls Mapbox / MapTiler / OSM Nominatim et les fonts
Google sont autorisés cross-origin. Pas de scripts inline (HTMX utilise
des attributs d'événement). Styles inline tolérés (CSS runtime Mapbox).

V3.1 : Stripe retiré de la facturation fret — NEWTOWT facture le fret par
virement bancaire. Réintroduit de façon **ciblée** pour la « vente à bord »
(encaissement CB des collaborateurs embarqués) : Stripe Checkout est une page
**hébergée** ouverte par le client sur son propre appareil, et le QR affiché
côté commandant est un SVG inline (segno). Aucune ressource Stripe n'est donc
embarquée dans nos pages → la CSP ci-dessous reste inchangée. (Si un jour on
intègre Stripe.js/Elements en direct, whitelister js.stripe.com / api.stripe.com
et le frame checkout.stripe.com.)
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
        # Pages HTML dynamiques : jamais mises en cache par le navigateur.
        # Évite qu'un formulaire périmé (ex. change-password sans token _csrf
        # d'une version antérieure) soit resservi depuis le cache → 403 CSRF.
        # Bonne pratique sécurité pour des pages authentifiées. Les assets
        # /static (text/css, application/javascript…) ne sont pas concernés.
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response
