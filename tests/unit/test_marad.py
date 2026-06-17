"""Tests du squelette d'intégration Marad (read-only)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.services import marad_sync
from app.utils import marad


def test_enabled_reflects_token(monkeypatch) -> None:
    monkeypatch.setattr(marad.settings, "marad_api_token", None)
    assert marad.enabled() is False
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret-key")
    assert marad.enabled() is True


def test_whitelist_blocks_non_read_endpoints() -> None:
    # Endpoints de lecture autorisés
    marad._assert_allowed("/api/Crewing")
    marad._assert_allowed("/api/CrewingDocuments/GetPassportDetails")
    # Endpoints d'écriture / hors whitelist → refusés (garde-fou read-only)
    for bad in ("/api/CrewingDocuments", "/api/CrewingSchedule/Update", "/api/anything"):
        with pytest.raises(ValueError):
            marad._assert_allowed(bad)


def test_records_normalisation() -> None:
    assert marad_sync._records(None) == []
    assert marad_sync._records([{"a": 1}, "x", {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert marad_sync._records({"data": [{"id": 1}]}) == [{"id": 1}]
    assert marad_sync._records({"id": 9}) == [{"id": 9}]


def test_vessel_map_parsing(monkeypatch) -> None:
    monkeypatch.setattr(marad.settings, "marad_vessel_map", "100=1, 200=2 ,bad")
    assert marad.vessel_map() == {"100": "1", "200": "2"}


def test_sync_crew_noop_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(marad, "enabled", lambda: False)

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                r = await marad_sync.sync_crew(s)
                assert r["configured"] is False
                assert r["fetched"] == 0
        finally:
            await eng.dispose()

    asyncio.run(_run())


def test_sync_crew_discovery_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _fake_list_crew(modified_since=None):
        return {"data": [{"id": 1, "firstName": "Anaïs", "lastName": "Mer", "rankId": 3}]}

    monkeypatch.setattr(marad, "list_crew", _fake_list_crew)

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                r = await marad_sync.sync_crew(s)
                assert r["configured"] is True
                assert r["fetched"] == 1
                assert r["mapped"] == 0  # pas d'écriture tant que le mapping n'est pas confirmé
                assert r["sample_fields"] == ["firstName", "id", "lastName", "rankId"]
        finally:
            await eng.dispose()

    asyncio.run(_run())
