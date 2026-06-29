"""EVO-01 (facturation hors plateforme) + UX-06 (polish charte).

EVO-01 / A5 : /me/invoices n'expose plus la liste ClientInvoice dormante mais
une page explicite « facturation gérée hors plateforme ».
UX-06 : `.empty-state` réintroduit dans kairos.css ; pages d'erreur 403/404
enrichies d'une icône Lucide.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_invoices_page_is_off_platform_notice():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "client/invoices.html")[0]
    assert "hors plateforme" in src
    assert "off-platform" in src
    assert "empty-state" in src


def test_invoices_route_renders_page_not_redirect():
    from app.routers import client_dashboard_router

    route = next(
        r for r in client_dashboard_router.router.routes if getattr(r, "path", "") == "/me/invoices"
    )
    # GET exposé (plus de 301 legacy)
    assert "GET" in route.methods


def test_empty_state_css_present():
    css = (_ROOT / "app/static/css/kairos.css").read_text(encoding="utf-8")
    assert ".empty-state" in css


def test_error_pages_have_lucide_icon():
    from app.templating import templates

    for page in ("errors/403.html", "errors/404.html"):
        src = templates.env.loader.get_source(templates.env, page)[0]
        assert "data-lucide" in src
