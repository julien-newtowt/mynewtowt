"""Pages d'erreur — toute erreur métier doit être lisible par un humain.

Constat de l'audit du 2026-08-27 : seuls 404 et 403 avaient un gabarit. Les
400 (vente sans montant, période clôturée, date invalide…), 409, 502 et 503
arrivaient à l'utilisateur en **JSON brut** sur fond blanc, sans mise en page
ni bouton retour. Sur un téléphone, devant un client qui paie, c'est un
cul-de-sac : on ne peut que retaper l'URL à la main.

Les appelants machine — API publique, webhooks — gardent du JSON.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient


def _build_app() -> FastAPI:
    """Construit l'application réelle.

    L'import était auparavant impossible depuis un test : `app/main.py` utilisait
    `@app.on_event`, déprécié par FastAPI, et la suite escalade les
    avertissements en erreurs (`pyproject.toml`). La fabrique de l'application
    n'était donc exercée nulle part — une troncature y serait passée inaperçue.
    Le passage aux gestionnaires de cycle de vie a levé l'obstacle.

    Le client n'entre pas dans le contexte de l'application : le cycle de vie
    (connexion à la base, ordonnanceur) n'a pas à tourner pour tester le rendu
    des erreurs.
    """
    from app.main import create_app

    return create_app()


@pytest.fixture(scope="module")
def client():
    app = _build_app()

    @app.get("/_test/boom")
    async def _boom(status: int = 400, detail: str = "Vente sans montant."):
        raise HTTPException(status_code=status, detail=detail)

    @app.get("/api/_test/boom")
    async def _api_boom():
        raise HTTPException(status_code=400, detail="paramètre absent")

    return TestClient(app, raise_server_exceptions=False)


def test_a_business_error_renders_an_html_page(client):
    r = client.get("/_test/boom", headers={"referer": "http://testserver/captain/ventes"})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "Action impossible" in body
    assert "Vente sans montant." in body  # le message métier est conservé
    assert "Revenir en arrière" in body  # …et il existe une issue
    assert '{"detail"' not in body


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "Action impossible"),
        (409, "Conflit"),
        (502, "Service externe indisponible"),
        (503, "Service indisponible"),
        (418, "Une erreur est survenue"),  # statut non listé → libellé de repli
    ],
)
def test_each_status_gets_a_readable_title(client, status, expected):
    r = client.get(f"/_test/boom?status={status}")
    assert r.status_code == status
    assert expected in r.text


def test_json_is_preserved_for_machine_callers(client):
    """L'API publique et les webhooks ne doivent pas recevoir du HTML."""
    r = client.get("/api/_test/boom")
    assert r.status_code == 400
    assert r.json() == {"detail": "paramètre absent"}

    r = client.get("/_test/boom", headers={"accept": "application/json"})
    assert r.json()["detail"] == "Vente sans montant."


def test_the_back_link_never_leaves_the_application(client):
    """Le Referer est repris tel quel : il ne doit pas pointer ailleurs."""
    r = client.get("/_test/boom", headers={"referer": "https://exemple-malveillant.tld/x"})
    assert "exemple-malveillant.tld" not in r.text
    assert "Revenir en arrière" not in r.text  # pas de lien plutôt qu'un mauvais


def test_the_back_link_is_omitted_when_it_points_to_the_failing_page(client):
    r = client.get("/_test/boom", headers={"referer": "http://testserver/_test/boom?status=400"})
    assert "Revenir en arrière" not in r.text


def test_404_and_403_keep_their_own_pages(client):
    r = client.get("/cette-page-nexiste-pas")
    assert r.status_code == 404
    assert "404" in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_generic_starlette_labels_are_not_echoed(client):
    """« Bad Request » n'apprend rien : le titre le dit déjà."""
    r = client.get("/_test/boom?detail=Bad%20Request")
    assert "Action impossible" in r.text
    assert "Bad Request" not in r.text


def test_the_stripe_webhook_still_answers_in_json(client, monkeypatch):
    """Garde-fou : le webhook doit rester lisible par Stripe, pas par un humain."""
    from app.services import stripe_checkout as sc

    monkeypatch.setattr(sc.settings, "stripe_webhook_secret", None)
    r = client.post("/webhooks/stripe", content=b"{}")
    assert r.status_code == 503
    assert r.json() == {"error": "not_configured"}


def test_the_app_is_fully_assembled(client):
    """Sentinelle : `create_app` doit rester complète.

    Le handler générique s'insère au milieu de la fabrique de l'application ;
    une insertion mal indentée y tronquerait silencieusement le reste (routeurs,
    middlewares, démarrage) sans qu'aucun lint ne le signale — le fichier
    resterait syntaxiquement valide.
    """
    app: FastAPI = client.app
    paths = {getattr(r, "path", "") for r in app.routes}
    for expected in ("/health", "/cashbox", "/captain/ventes", "/webhooks/stripe"):
        assert expected in paths, f"route absente : {expected}"
    assert any(m.cls.__name__ == "CSRFMiddleware" for m in app.user_middleware)


# ── PWA du bord — périmètre du service worker ───────────────────────────────


def test_the_service_worker_covers_the_sales_and_cashbox_module():
    """L'audit relevait que le module encaissant de l'argent en mer vivait hors
    du périmètre du service worker, alors que la notice promettait l'inverse."""
    sw = Path("app/static/sw.js").read_text(encoding="utf-8")
    for path in ("/onboard", "/captain/ventes", "/cashbox", "/static/"):
        assert f'"{path}"' in sw, f"périmètre absent du service worker : {path}"
    # Les POST ne doivent jamais être interceptés : la file IndexedDB s'en charge.
    assert 'request.method !== "GET"' in sw


def test_the_quick_sale_script_is_precached():
    sw = Path("app/static/sw.js").read_text(encoding="utf-8")
    assert "/static/js/onboard-quick-sale.js" in sw


def test_the_quick_sale_form_is_queued_offline():
    """Sans `data-offline-queue`, la file d'attente ne voit pas le formulaire."""
    page = Path("app/templates/staff/onboard_sales/vessel.html").read_text(encoding="utf-8")
    assert "data-offline-queue" in page
    assert "vente-rapide" in page
    # La PWA doit être chargée sur cet écran, sinon rien ne s'enregistre.
    for script in ("pwa-onboard.js", "onboard-idb.js", "onboard-offline.js"):
        assert script in page


def test_no_deprecated_event_handlers_remain():
    """`@app.on_event` rendait l'application intestable — qu'il ne revienne pas.

    On cherche le **décorateur** en début de ligne, pas la chaîne : les
    docstrings du module le mentionnent légitimement pour expliquer la
    migration.
    """
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*@\w+\.on_event\(", source, re.M)
    assert "lifespan=" in source
