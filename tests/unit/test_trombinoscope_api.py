"""Unitaires — endpoint cron trombinoscope (`POST /api/trombinoscope/generate`).

Couvre uniquement le contrôle d'accès (503 si token non configuré, 403 si
invalide), sur le même patron que ``tests/unit/test_marad.py`` (TestClient +
override de ``get_db``, sans DB réelle nécessaire — ces chemins échouent
avant tout accès base). Le chemin de succès (génération + archivage) est
couvert par tests/integration/test_report_archive.py et
tests/integration/test_crew_directory.py (build_directory), qui nécessitent
une vraie session DB.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.database import get_db
from app.routers.crew_router import trombinoscope_api_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(trombinoscope_api_router)
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app)


def test_generate_returns_503_when_token_not_configured(monkeypatch) -> None:
    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "trombinoscope_api_token", None)
    resp = _client().post("/api/trombinoscope/generate", headers={"X-API-Token": "whatever"})
    assert resp.status_code == 503


def test_generate_returns_403_when_token_invalid(monkeypatch) -> None:
    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "trombinoscope_api_token", "tok-secret")
    resp = _client().post("/api/trombinoscope/generate", headers={"X-API-Token": "wrong"})
    assert resp.status_code == 403


def test_generate_returns_403_when_token_missing(monkeypatch) -> None:
    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "trombinoscope_api_token", "tok-secret")
    resp = _client().post("/api/trombinoscope/generate")
    assert resp.status_code == 403
