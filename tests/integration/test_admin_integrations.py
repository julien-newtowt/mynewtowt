"""ADM-07 — écran d'intégrations externes (Pipedrive) : état + test de connexion.

Vérifie que l'écran `/admin/integrations` expose l'état de l'intégration
Pipedrive et un test de connectivité (sans manipuler le secret côté UI — le
jeton reste piloté par `PIPEDRIVE_API_TOKEN`), et que le test renvoie un badge
« non configuré » quand aucun jeton n'est présent (aucun appel réseau).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.integration.conftest import FakeRequest


class _ReqState:
    """Requête minimale avec .state/.cookies/.query_params (pour le rendu de
    partiels HTMX qui traversent le context processor de templating)."""

    headers: dict = {}
    cookies: dict = {}
    query_params: dict = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/admin/integrations")
    state = SimpleNamespace(lang="fr")

    async def form(self):
        return {}


def test_integrations_template_exposes_test_action():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/admin/integrations.html")[0]
    assert 'hx-post="/admin/integrations/pipedrive/test"' in src
    # Le secret n'est pas saisi en UI : on documente la source de vérité env.
    assert "PIPEDRIVE_API_TOKEN" in src


def test_integrations_template_exposes_marad_card():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/admin/integrations.html")[0]
    assert 'hx-post="/admin/integrations/marad/test"' in src
    assert "MARAD_API_TOKEN" in src


def test_sidebar_exposes_integrations_link():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/_layout.html")[0]
    assert "/admin/integrations" in src


def test_integrations_routes_registered():
    from app.routers import admin_router

    paths = {r.path for r in admin_router.router.routes}
    assert "/admin/integrations" in paths
    assert "/admin/integrations/pipedrive/test" in paths
    assert "/admin/integrations/marad/test" in paths


@pytest.mark.asyncio
async def test_marad_test_without_token_reports_unconfigured(db, staff_user):
    """Sans jeton, diagnose() renvoie 'not_configured' sans réseau → badge warn."""
    from app.routers.admin_router import integrations_marad_test
    from app.utils import marad

    resp = await integrations_marad_test(_ReqState(), db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.template.name == "staff/admin/_marad_test_result.html"
    diag = resp.context["diag"]
    if not marad.enabled():
        assert diag["classification"] == "not_configured"
        assert diag["configured"] is False


@pytest.mark.asyncio
async def test_pipedrive_test_without_token_reports_unconfigured(db, staff_user):
    """Sans jeton, ping() renvoie False sans réseau → badge « non configuré »."""
    from app.routers.admin_router import integrations_pipedrive_test

    resp = await integrations_pipedrive_test(FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.template.name == "staff/admin/_integration_test_result.html"
    # Robuste à l'environnement : sans jeton, ping() est False sans réseau ;
    # si un jeton est configuré en CI, on n'impose pas le résultat réseau.
    assert isinstance(resp.context["ok"], bool)
    from app.utils import pipedrive

    if not pipedrive.enabled():
        assert resp.context["ok"] is False
        assert resp.context["configured"] is False
