"""Tests d'intégration — cron FLGO Marad (LECTURE SEULE), MRV LOT 7.

Patron ``tests/unit/test_marad.py::test_refresh_endpoint_only_param_decouples_crew_and_schedules``
(TestClient + ``get_db`` overridé) — le token dédié ``MARAD_FLGO_TOKEN`` gate
``POST /api/marad/flgo-refresh`` indépendamment de ``MARAD_SYNC_TOKEN`` (crew).
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.database import get_db
from app.routers import marad_router
from app.routers.marad_router import api_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app)


def test_flgo_refresh_route_registered():
    paths = {route.path for route in api_router.routes}
    assert "/api/marad/flgo-refresh" in paths


def test_flgo_refresh_503_without_token(monkeypatch):
    monkeypatch.setattr(marad_router.settings, "marad_flgo_token", None)
    r = _client().post("/api/marad/flgo-refresh", headers={"X-API-Token": "whatever"})
    assert r.status_code == 503


def test_flgo_refresh_401_wrong_token(monkeypatch):
    monkeypatch.setattr(marad_router.settings, "marad_flgo_token", "correct-token")
    r = _client().post("/api/marad/flgo-refresh", headers={"X-API-Token": "wrong"})
    assert r.status_code == 401


def test_flgo_refresh_401_missing_header(monkeypatch):
    monkeypatch.setattr(marad_router.settings, "marad_flgo_token", "correct-token")
    r = _client().post("/api/marad/flgo-refresh")
    assert r.status_code == 401


def test_flgo_refresh_independent_from_crew_sync_token(monkeypatch):
    """MARAD_FLGO_TOKEN et MARAD_SYNC_TOKEN sont deux secrets DISTINCTS — le
    token crew ne doit jamais authentifier le cron FLGO."""
    monkeypatch.setattr(marad_router.settings, "marad_flgo_token", "flgo-secret")
    monkeypatch.setattr(marad_router.settings, "marad_sync_token", "crew-secret")
    r = _client().post("/api/marad/flgo-refresh", headers={"X-API-Token": "crew-secret"})
    assert r.status_code == 401


def test_flgo_refresh_200_with_mocked_client(monkeypatch):
    monkeypatch.setattr(marad_router.settings, "marad_flgo_token", "tok")

    async def _fake_sync(db, **kw):
        return {
            "configured": True,
            "fetched": 5,
            "imported": 3,
            "updated": 2,
            "skipped": 0,
            "errors": 0,
            "note": "ok",
            "vessels_synced": ["ANE"],
        }

    async def _fake_activity(*a, **kw):
        return None

    monkeypatch.setattr(marad_router.flgo_sync, "sync_flgo_from_api", _fake_sync)
    monkeypatch.setattr(marad_router, "activity_record", _fake_activity)

    r = _client().post("/api/marad/flgo-refresh", headers={"X-API-Token": "tok"})
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 3
    assert body["updated"] == 2
    assert body["skipped"] == 0
    assert body["errors"] == 0


def test_flgo_refresh_502_on_api_failure_no_unhandled_exception(monkeypatch):
    """Panne du client API (exception non gérée, ex. simulée par ce mock) →
    502 propre. Le point du test : TestClient (raise_server_exceptions=True
    par défaut) re-lèverait toute exception NON catchée par le routeur — ce
    test échouerait donc si le try/except du routeur disparaissait."""
    monkeypatch.setattr(marad_router.settings, "marad_flgo_token", "tok")

    async def _boom(db, **kw):
        raise RuntimeError("panne API Marad simulée")

    monkeypatch.setattr(marad_router.flgo_sync, "sync_flgo_from_api", _boom)

    r = _client().post("/api/marad/flgo-refresh", headers={"X-API-Token": "tok"})
    assert r.status_code == 502
