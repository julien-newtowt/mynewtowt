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


# Échantillon conforme au schéma réel /api/Crewing (GUID + ranks[] + adresses…).
_GUID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def _crew_record(**over) -> dict:
    rec = {
        "id": _GUID,
        "firstName": "Jean",
        "lastName": "Dupont",
        "callName": "JD",
        "gender": 0,
        "birthDate": "1988-03-12T00:00:00Z",
        "nationality": "FR",
        "ranks": ["Capitaine", "Second"],
        "email": "jean.dupont@example.com",
        "mobilePhone": "+33 6 12 34 56 78",
        "phone": "",
        "idNumber": "ID-123",
        "bankAccount": "FR7612345678901234567890123",  # sensible — ne doit JAMAIS être stocké
        "vesselNames": ["Anemos"],
    }
    rec.update(over)
    return rec


def _run_with_db(coro_factory):
    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                await coro_factory(s)
        finally:
            await eng.dispose()

    asyncio.run(_run())


def test_mapping_helpers_reject_swagger_placeholders() -> None:
    # Le record exact renvoyé par Marad en exemple = placeholders "string".
    ph = {
        "id": _GUID,
        "firstName": "string",
        "lastName": "string",
        "callName": "string",
        "nationality": "string",
        "ranks": ["string"],
        "email": "string",
        "mobilePhone": "string",
        "birthDate": "2026-06-17T16:36:10.068Z",
    }
    assert marad_sync._full_name(ph) is None
    assert marad_sync._first_rank(ph) is None
    assert marad_sync._nationality(ph) is None
    assert marad_sync._email(ph) is None
    assert marad_sync._phone(ph) is None
    # …mais l'id (GUID) et la date de naissance restent exploitables.
    assert marad_sync._birth_date(ph).year == 2026


def test_sync_crew_creates_and_maps(monkeypatch) -> None:
    from datetime import date

    from app.models.crew import CrewMember

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _fake_list_crew(modified_since=None):
        return [_crew_record()]

    monkeypatch.setattr(marad, "list_crew", _fake_list_crew)

    async def _check(s):
        from sqlalchemy import select

        r = await marad_sync.sync_crew(s)
        assert r == {
            "configured": True,
            "fetched": 1,
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "note": r["note"],
        }
        m = (
            await s.execute(select(CrewMember).where(CrewMember.marad_id == _GUID))
        ).scalar_one()
        assert m.full_name == "Jean Dupont"
        assert m.role == "Capitaine"  # premier rang
        assert m.nationality == "FR"
        assert m.date_of_birth == date(1988, 3, 12)
        assert m.email == "jean.dupont@example.com"
        assert m.phone == "+33 6 12 34 56 78"  # mobilePhone prioritaire
        assert m.is_active is True
        # Champ sensible : jamais stocké (aucune colonne, et pas dans notes).
        assert "FR7612345678901234567890123" not in (m.notes or "")

    _run_with_db(_check)


def test_sync_crew_idempotent_and_preserves_erp_fields(monkeypatch) -> None:
    from datetime import date

    from app.models.crew import CrewMember

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        # 1er passage : création
        monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([_crew_record()]))
        r1 = await marad_sync.sync_crew(s)
        assert (r1["created"], r1["updated"]) == (1, 0)

        # Un champ géré par l'ERP (hors périmètre Marad) est renseigné à la main.
        m = (
            await s.execute(select(CrewMember).where(CrewMember.marad_id == _GUID))
        ).scalar_one()
        m.schengen_status = "warning"
        m.visa_br_expires_at = date(2027, 1, 1)
        await s.flush()

        # 2e passage : nom modifié côté Marad → mise à jour, pas de doublon.
        monkeypatch.setattr(
            marad,
            "list_crew",
            lambda modified_since=None: _ret([_crew_record(lastName="Durand")]),
        )
        r2 = await marad_sync.sync_crew(s)
        assert (r2["created"], r2["updated"]) == (0, 1)

        all_members = (await s.execute(select(CrewMember))).scalars().all()
        assert len(all_members) == 1  # idempotent : un seul enregistrement
        m2 = all_members[0]
        assert m2.full_name == "Jean Durand"  # rafraîchi depuis Marad
        # Champs ERP préservés (jamais écrasés par la sync).
        assert m2.schengen_status == "warning"
        assert m2.visa_br_expires_at == date(2027, 1, 1)

    _run_with_db(_check)


def test_sync_crew_placeholder_does_not_clobber(monkeypatch) -> None:
    from app.models.crew import CrewMember

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([_crew_record()]))
        await marad_sync.sync_crew(s)

        # Marad renvoie ensuite un record « vide » (placeholders) pour le même GUID :
        # les bonnes valeurs déjà présentes ne doivent pas être effacées.
        empty = {"id": _GUID, "firstName": "string", "lastName": "string", "ranks": ["string"]}
        monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([empty]))
        await marad_sync.sync_crew(s)

        m = (
            await s.execute(select(CrewMember).where(CrewMember.marad_id == _GUID))
        ).scalar_one()
        assert m.full_name == "Jean Dupont"  # conservé
        assert m.role == "Capitaine"  # conservé
        assert m.email == "jean.dupont@example.com"  # conservé

    _run_with_db(_check)


def test_sync_crew_skips_records_without_id(monkeypatch) -> None:
    from app.models.crew import CrewMember

    monkeypatch.setattr(marad, "enabled", lambda: True)
    monkeypatch.setattr(
        marad, "list_crew", lambda modified_since=None: _ret([{"firstName": "X", "lastName": "Y"}])
    )

    async def _check(s):
        from sqlalchemy import select

        r = await marad_sync.sync_crew(s)
        assert (r["fetched"], r["created"], r["skipped"]) == (1, 0, 1)
        assert (await s.execute(select(CrewMember))).scalars().all() == []

    _run_with_db(_check)


async def _ret(value):
    """Petite coroutine helper pour les fakes de ``marad.list_crew``."""
    return value
