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
        m = (await s.execute(select(CrewMember).where(CrewMember.marad_id == _GUID))).scalar_one()
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
        m = (await s.execute(select(CrewMember).where(CrewMember.marad_id == _GUID))).scalar_one()
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

        m = (await s.execute(select(CrewMember).where(CrewMember.marad_id == _GUID))).scalar_one()
        assert m.full_name == "Jean Dupont"  # conservé
        assert m.role == "Capitaine"  # conservé
        assert m.email == "jean.dupont@example.com"  # conservé

    _run_with_db(_check)


def test_sync_crew_accepts_pascalcase_fields(monkeypatch) -> None:
    """Les serveurs par tenant (external02.marad.ms) sérialisent en PascalCase.

    Schéma constaté en production (requêtes Power BI sur /api/Crewing) :
    ``ID``, ``FirstName``, ``LastName``, ``BirthDate``, ``Ranks``… Avant le
    correctif, ``rec.get("id")`` ne trouvait pas ``ID`` → 100 % des marins
    étaient « skipped » alors que l'API répondait correctement.
    """
    from datetime import date

    from app.models.crew import CrewMember

    monkeypatch.setattr(marad, "enabled", lambda: True)
    pascal = {
        "ID": _GUID,
        "CallName": "JD",
        "FirstName": "Jean",
        "LastName": "Dupont",
        "Gender": 0,
        "BirthDate": "1988-03-12T00:00:00Z",
        "Nationality": "FR",
        "Ranks": ["Capitaine"],
        "Email": "jean.dupont@example.com",
        "MobilePhone": "+33 6 12 34 56 78",
        "Phone": "",
        "VesselNames": ["Anemos"],
    }
    monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([pascal]))

    async def _check(s):
        from sqlalchemy import select

        r = await marad_sync.sync_crew(s)
        assert (r["fetched"], r["created"], r["skipped"]) == (1, 1, 0)
        m = (await s.execute(select(CrewMember).where(CrewMember.marad_id == _GUID))).scalar_one()
        assert m.full_name == "Jean Dupont"
        assert m.role == "Capitaine"
        assert m.nationality == "FR"
        assert m.date_of_birth == date(1988, 3, 12)
        assert m.email == "jean.dupont@example.com"
        assert m.phone == "+33 6 12 34 56 78"

    _run_with_db(_check)


def test_sync_schedules_accepts_pascalcase_fields(monkeypatch) -> None:
    """Même tolérance de casse pour /api/CrewingSchedule (objets imbriqués)."""
    from datetime import UTC, date, datetime

    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.models.leg import Leg
    from app.models.vessel import Vessel

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        vessel = Vessel(code="CF", name="Anemos")
        s.add(vessel)
        await s.flush()
        leg = Leg(
            leg_code="1CFRBR6",
            vessel_id=vessel.id,
            departure_port_id=1,
            arrival_port_id=1,
            etd_ref=datetime(2026, 3, 1, tzinfo=UTC),
            eta_ref=datetime(2026, 3, 10, tzinfo=UTC),
            etd=datetime(2026, 3, 1, tzinfo=UTC),
            eta=datetime(2026, 3, 10, tzinfo=UTC),
        )
        member = CrewMember(marad_id="crew-guid-1", full_name="Jean Dupont", role="capitaine")
        s.add_all([leg, member])
        await s.flush()

        pascal = {
            "ID": "sched-1",
            "CrewMember": {"ID": "crew-guid-1", "FirstName": "Jean", "LastName": "Dupont"},
            "Rank": "Capitaine",
            "Status": "Confirmed",
            "Vessel": "Anemos",
            "StartInfo": {"DateTime": "2026-03-05T00:00:00Z", "Port": "Fécamp"},
            "EndInfo": {"DateTime": "2026-03-09T00:00:00Z", "Port": "Fortaleza"},
        }
        monkeypatch.setattr(marad, "list_schedules", lambda modified_since=None: _ret([pascal]))

        r = await marad_sync.sync_schedules(s)
        assert (r["fetched"], r["created"], r["skipped"]) == (1, 1, 0)
        row = (
            await s.execute(
                select(MaradCrewSchedule).where(MaradCrewSchedule.marad_schedule_id == "sched-1")
            )
        ).scalar_one()
        assert row.crew_member_id == member.id
        assert row.vessel_id == vessel.id
        assert row.leg_id == leg.id
        assert row.marad_voyage_ref == "Fécamp → Fortaleza"
        assert row.rank_label == "Capitaine"
        assert row.start_date == date(2026, 3, 5)
        assert row.end_date == date(2026, 3, 9)
        assert row.status == "Confirmed"

    _run_with_db(_check)


def test_records_accepts_pascalcase_wrapper() -> None:
    """L'enveloppe éventuelle (``Data``/``Value``…) tolère aussi le PascalCase."""
    assert marad_sync._records({"Data": [{"ID": 1}]}) == [{"ID": 1}]
    assert marad_sync._records({"ID": 9}) == [{"ID": 9}]


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
    """Petite coroutine helper pour les fakes de ``marad.list_crew`` / ``list_schedules``."""
    return value


def _schedule_record(**over) -> dict:
    """Échantillon conforme au schéma réel /api/CrewingSchedule (objets imbriqués)."""
    rec = {
        "id": "sched-1",
        "crewMember": {"id": "crew-guid-1", "firstName": "Jean", "lastName": "Dupont"},
        "rank": "Capitaine",
        "status": "Confirmed",
        "vessel": "Anemos",  # NOM du navire (pas un id)
        "startInfo": {"dateTime": "2026-03-05T00:00:00Z", "port": "Fécamp"},
        "endInfo": {"dateTime": "2026-03-09T00:00:00Z", "port": "Fortaleza"},
    }
    rec.update(over)
    return rec


def test_sync_schedules_resolves_vessel_and_leg(monkeypatch) -> None:
    from datetime import UTC, date, datetime

    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.models.leg import Leg
    from app.models.vessel import Vessel

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        # Référentiels locaux : navire (nom) + leg (fenêtre de dates) + marin (marad_id).
        vessel = Vessel(code="CF", name="Anemos")
        s.add(vessel)
        await s.flush()
        leg = Leg(
            leg_code="1CFRBR6",
            vessel_id=vessel.id,
            departure_port_id=1,
            arrival_port_id=1,
            etd_ref=datetime(2026, 3, 1, tzinfo=UTC),
            eta_ref=datetime(2026, 3, 10, tzinfo=UTC),
            etd=datetime(2026, 3, 1, tzinfo=UTC),
            eta=datetime(2026, 3, 10, tzinfo=UTC),
        )
        member = CrewMember(marad_id="crew-guid-1", full_name="Jean Dupont", role="capitaine")
        s.add_all([leg, member])
        await s.flush()

        monkeypatch.setattr(
            marad, "list_schedules", lambda modified_since=None: _ret([_schedule_record()])
        )

        r = await marad_sync.sync_schedules(s)
        assert (r["configured"], r["fetched"], r["created"], r["updated"]) == (True, 1, 1, 0)

        row = (
            await s.execute(
                select(MaradCrewSchedule).where(MaradCrewSchedule.marad_schedule_id == "sched-1")
            )
        ).scalar_one()
        assert row.crew_member_id == member.id  # crewMember.id ↔ marad_id
        assert row.vessel_id == vessel.id  # résolu via le nom du navire
        assert row.leg_id == leg.id  # voyage = leg : fenêtre de dates du navire
        assert row.marad_vessel_name == "Anemos"
        assert row.marad_voyage_ref == "Fécamp → Fortaleza"  # route POL→POD
        assert row.rank_label == "Capitaine"
        assert row.start_date == date(2026, 3, 5)
        assert row.end_date == date(2026, 3, 9)
        assert row.status == "Confirmed"

        # 2e passage : idempotent (pas de doublon).
        r2 = await marad_sync.sync_schedules(s)
        assert (r2["created"], r2["updated"]) == (0, 1)
        assert len((await s.execute(select(MaradCrewSchedule))).scalars().all()) == 1

    _run_with_db(_check)


def test_sync_schedules_tenant_shape_no_ids_matches_by_name(monkeypatch) -> None:
    """Schéma RÉEL du tenant (external02) : ni id de planning, ni GUID marin.

    Le planning porte ``CrewMember: {FirstName, LastName, EmployeeNumber, …}``
    sans ``ID``, et pas d'``ID`` au niveau racine. Avant le correctif : 100 %
    des plannings étaient « skipped » (pas d'id) et jamais rattachés à un marin
    (pas de GUID). Après : clé synthétique stable + rapprochement par nom.
    """
    from datetime import date

    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.models.vessel import Vessel

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        vessel = Vessel(code="CF", name="Anemos")
        # Le marin existe côté ERP, importé sans GUID (marad_id NULL) mais nommé.
        member = CrewMember(full_name="Jean Dupont", role="capitaine")
        s.add_all([vessel, member])
        await s.flush()

        # Record tel que le renvoie external02 : PascalCase, aucun id, aucun GUID.
        rec = {
            "CrewMember": {
                "FirstName": "Jean",
                "LastName": "Dupont",
                "EmployeeNumber": "EMP-42",
                "IDNumber": "ID-999",
            },
            "Rank": "Capitaine",
            "Status": "Confirmed",
            "Vessel": "Anemos",
            "StartInfo": {"DateTime": "2026-03-05T00:00:00Z", "Port": "Fécamp"},
            "EndInfo": {"DateTime": "2026-03-09T00:00:00Z", "Port": "Fortaleza"},
        }
        monkeypatch.setattr(marad, "list_schedules", lambda modified_since=None: _ret([rec]))

        r = await marad_sync.sync_schedules(s)
        assert (r["fetched"], r["created"], r["skipped"]) == (1, 1, 0)
        row = (await s.execute(select(MaradCrewSchedule))).scalar_one()
        assert row.crew_member_id == member.id  # rattaché PAR NOM (pas de GUID)
        assert row.marad_crew_id is None  # aucun GUID fourni par ce tenant
        assert row.vessel_id == vessel.id
        assert row.rank_label == "Capitaine"
        assert row.start_date == date(2026, 3, 5)
        assert row.end_date == date(2026, 3, 9)
        assert row.status == "Confirmed"
        assert row.marad_schedule_id.startswith("syn-")  # clé synthétique

        # 2e passage : clé synthétique stable → mise à jour, pas de doublon.
        r2 = await marad_sync.sync_schedules(s)
        assert (r2["created"], r2["updated"]) == (0, 1)
        assert len((await s.execute(select(MaradCrewSchedule))).scalars().all()) == 1

    _run_with_db(_check)


def test_end_to_end_crew_then_schedules_links_activity_to_member(monkeypatch) -> None:
    """Flux RÉEL de production : le marin est créé par la sync crew (Marad fournit
    le GUID), PUIS le planning — qui n'a NI id NI GUID, seulement le nom — doit
    se rattacher à ce marin.

    C'est la garantie « liaison activité ↔ équipage » : aucun marin n'est créé
    depuis l'appli, tous viennent de Marad ; la seule clé commune entre
    /api/Crewing (avec GUID) et /api/CrewingSchedule (sans GUID) est le nom.
    """
    from datetime import date

    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.models.vessel import Vessel

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        s.add(Vessel(code="CF", name="Anemos"))
        await s.flush()

        # 1) Sync crew — schéma réel /api/Crewing (PascalCase, AVEC GUID).
        crew_rec = {
            "ID": _GUID,
            "FirstName": "Élise",
            "LastName": "Le Goff",
            "Ranks": ["Capitaine"],
            "Nationality": "FR",
        }
        monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([crew_rec]))
        rc = await marad_sync.sync_crew(s)
        assert (rc["created"], rc["skipped"]) == (1, 0)
        member = (await s.execute(select(CrewMember))).scalar_one()
        assert member.marad_id == _GUID and member.full_name == "Élise Le Goff"

        # 2) Sync schedules — schéma réel /api/CrewingSchedule (PascalCase, SANS
        #    id de planning, SANS GUID marin ; le même nom, orthographié pareil).
        sched_rec = {
            "CrewMember": {
                "FirstName": "Élise",
                "LastName": "Le Goff",
                "EmployeeNumber": "EMP-7",
            },
            "Rank": "Capitaine",
            "Status": "Confirmed",
            "Vessel": "Anemos",
            "StartInfo": {"DateTime": "2026-05-02T00:00:00Z", "Port": "Fécamp"},
            "EndInfo": {"DateTime": "2026-05-14T00:00:00Z", "Port": "Fortaleza"},
        }
        monkeypatch.setattr(marad, "list_schedules", lambda modified_since=None: _ret([sched_rec]))
        rs = await marad_sync.sync_schedules(s)
        assert (rs["created"], rs["skipped"]) == (1, 0)

        row = (await s.execute(select(MaradCrewSchedule))).scalar_one()
        # LIAISON VÉRIFIÉE : le planning pointe sur le marin créé par la sync crew.
        assert row.crew_member_id == member.id
        assert row.start_date == date(2026, 5, 2)
        assert row.end_date == date(2026, 5, 14)
        assert row.rank_label == "Capitaine"

    _run_with_db(_check)


def test_sync_all_crew_before_schedules_so_linkage_works(monkeypatch) -> None:
    """sync_all doit synchroniser le crew AVANT les plannings, sinon le
    rapprochement par nom échouerait (le marin n'existerait pas encore)."""
    monkeypatch.setattr(marad, "enabled", lambda: True)

    order: list[str] = []

    async def _list_crew(modified_since=None):
        order.append("crew")
        return [{"ID": _GUID, "FirstName": "Ana", "LastName": "Silva", "Ranks": ["Second"]}]

    async def _list_schedules(modified_since=None):
        order.append("schedules")
        return [
            {
                "CrewMember": {"FirstName": "Ana", "LastName": "Silva"},
                "Vessel": "Anemos",
                "StartInfo": {"DateTime": "2026-06-01T00:00:00Z"},
            }
        ]

    monkeypatch.setattr(marad, "list_crew", _list_crew)
    monkeypatch.setattr(marad, "list_schedules", _list_schedules)

    async def _check(s):
        from sqlalchemy import select

        from app.models.crew import CrewMember, MaradCrewSchedule

        r = await marad_sync.sync_all(s)
        assert order == ["crew", "schedules"]  # ordre garant de la liaison
        assert r["crew_created"] == 1 and r["sched_created"] == 1
        member = (await s.execute(select(CrewMember))).scalar_one()
        row = (await s.execute(select(MaradCrewSchedule))).scalar_one()
        assert row.crew_member_id == member.id  # activité rattachée à l'équipe

    _run_with_db(_check)


def test_sync_schedules_real_tenant_shape_with_guids_and_leave(monkeypatch) -> None:
    """Données RÉELLES external02 : le planning porte bien un ID et
    crewMember.ID (le PBIX ne les affichait pas). Rattachement PAR GUID, clé =
    l'ID réel (pas synthétique). Cas « Congés » : Vessel=null, ports vides →
    ligne créée et rattachée au marin, sans navire ni voyage.
    """
    from datetime import date

    from app.models.crew import CrewMember, MaradCrewSchedule

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        # Marin importé par la sync crew, clé = son GUID Marad.
        member = CrewMember(
            marad_id="09f0c439-74c5-467d-ad96-cc42e61fb9cd",
            full_name="Ousseynou BOUSSO",
            role="Chief Engineer",
        )
        s.add(member)
        await s.flush()

        rec = {
            "ID": "cd82cd52-0c00-4979-ae03-544136753361",
            "CrewMember": {
                "ID": "09f0c439-74c5-467d-ad96-cc42e61fb9cd",
                "FirstName": "Ousseynou",
                "LastName": "BOUSSO",
                "EmployeeNumber": "00080",
                "IDNumber": "A02916306",
            },
            "Rank": "Chief Engineer (Migrated)",
            "Status": "Congés",
            "Vessel": None,
            "StartInfo": {"DateTime": "2026-05-15T00:00:00", "Port": ""},
            "EndInfo": {"DateTime": "2026-07-09T23:59:00", "Port": ""},
        }
        monkeypatch.setattr(marad, "list_schedules", lambda modified_since=None: _ret([rec]))

        r = await marad_sync.sync_schedules(s)
        assert (r["fetched"], r["created"], r["skipped"]) == (1, 1, 0)
        row = (await s.execute(select(MaradCrewSchedule))).scalar_one()
        assert row.marad_schedule_id == "cd82cd52-0c00-4979-ae03-544136753361"  # id réel
        assert row.crew_member_id == member.id  # rattaché PAR GUID
        assert row.marad_crew_id == "09f0c439-74c5-467d-ad96-cc42e61fb9cd"
        assert row.vessel_id is None and row.marad_vessel_name is None  # congé, pas d'embarquement
        assert row.marad_voyage_ref is None  # ports vides
        assert row.status == "Congés"
        assert row.start_date == date(2026, 5, 15)
        assert row.end_date == date(2026, 7, 9)

    _run_with_db(_check)


def test_sync_all_retries_schedules_after_wait_on_429(monkeypatch) -> None:
    """Cron : /api/CrewingSchedule en 429 (appelé juste après /api/Crewing) →
    on patiente puis on retente UNE fois, et les plannings remontent alors.
    Le crew n'est PAS rappelé (économie de quota) et on ne dort pas réellement.
    """
    monkeypatch.setattr(marad, "enabled", lambda: True)
    monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([_crew_record()]))

    calls = {"sched": 0}
    statuses = iter([429, 200])  # 1er appel throttlé, 2e OK

    async def _list_schedules(modified_since=None):
        calls["sched"] += 1
        st = next(statuses)
        marad._last_status["/api/CrewingSchedule"] = st
        return [] if st == 429 else [{"id": "s1", "vessel": "X"}]

    monkeypatch.setattr(marad, "_last_status", {})
    monkeypatch.setattr(marad, "list_schedules", _list_schedules)

    slept: list[float] = []

    async def _fake_sleep(sec):
        slept.append(sec)

    monkeypatch.setattr(marad_sync.asyncio, "sleep", _fake_sleep)

    async def _check(s):
        r = await marad_sync.sync_all(s, schedule_retry_wait=65)
        assert calls["sched"] == 2  # 1 échec (429) + 1 retry réussi
        assert slept == [65]  # a bien patienté une fois
        assert r["sched_fetched"] == 1 and r["sched_created"] == 1
        assert r["crew_fetched"] == 1  # crew NON rappelé

    _run_with_db(_check)


def test_sync_all_no_retry_when_wait_is_zero(monkeypatch) -> None:
    """Bouton (wait=0) : pas de retry — le 429 est simplement remonté."""
    monkeypatch.setattr(marad, "enabled", lambda: True)
    monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([_crew_record()]))
    calls = {"sched": 0}

    async def _list_schedules(modified_since=None):
        calls["sched"] += 1
        marad._last_status["/api/CrewingSchedule"] = 429
        return None

    monkeypatch.setattr(marad, "_last_status", {})
    monkeypatch.setattr(marad, "list_schedules", _list_schedules)
    monkeypatch.setattr(marad, "diagnose", lambda: _ret({}))

    async def _check(s):
        r = await marad_sync.sync_all(s)  # wait=0 par défaut
        assert calls["sched"] == 1  # aucun retry
        assert r["sched_fetched"] == 0
        assert "CrewingSchedule a renvoyé 429" in (r["diagnostic"] or "")

    _run_with_db(_check)


def test_sync_all_surfaces_schedule_429_on_partial_success(monkeypatch) -> None:
    """Crew OK mais /api/CrewingSchedule en 429 → le message ne doit PAS masquer
    l'absence de plannings derrière le succès du crew (le trou « N marins, 0
    planning » sans explication)."""
    monkeypatch.setattr(marad, "enabled", lambda: True)
    monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([_crew_record()]))
    monkeypatch.setattr(marad, "list_schedules", lambda modified_since=None: _ret(None))
    monkeypatch.setattr(
        marad, "last_status", lambda p: 429 if p == "/api/CrewingSchedule" else 200
    )

    async def _no_diagnose():
        raise AssertionError("diagnose() ne doit pas être appelé (crew a réussi)")

    monkeypatch.setattr(marad, "diagnose", _no_diagnose)

    async def _check(s):
        r = await marad_sync.sync_all(s)
        assert r["crew_fetched"] == 1 and r["sched_fetched"] == 0
        assert r["diagnostic"] and "CrewingSchedule a renvoyé 429" in r["diagnostic"]

    _run_with_db(_check)


def test_sync_schedules_name_match_ignores_accents_and_case(monkeypatch) -> None:
    """Le rapprochement par nom tolère casse et accents (« MÜLLER » ↔ « Müller »)."""
    from app.models.crew import CrewMember, MaradCrewSchedule

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        member = CrewMember(full_name="José Müller", role="second")
        s.add(member)
        await s.flush()
        rec = {
            "CrewMember": {"FirstName": "JOSE", "LastName": "MULLER"},
            "Vessel": "Artemis",
            "StartInfo": {"DateTime": "2026-04-01T00:00:00Z"},
        }
        monkeypatch.setattr(marad, "list_schedules", lambda modified_since=None: _ret([rec]))
        await marad_sync.sync_schedules(s)
        row = (await s.execute(select(MaradCrewSchedule))).scalar_one()
        assert row.crew_member_id == member.id

    _run_with_db(_check)


def test_synthetic_key_stable_and_distinct() -> None:
    """La clé synthétique est déterministe (même entrée → même clé) et
    discrimine marin/navire/début ; ≤ 36 car. (colonne marad_schedule_id)."""
    cm = {"FirstName": "Jean", "LastName": "Dupont"}
    k1 = marad_sync._synthetic_sched_key(cm, "Anemos", "2026-03-05T00:00:00Z")
    k2 = marad_sync._synthetic_sched_key(cm, "Anemos", "2026-03-05T00:00:00Z")
    assert k1 == k2 and len(k1) <= 36 and k1.startswith("syn-")
    # Navire différent → clé différente.
    assert k1 != marad_sync._synthetic_sched_key(cm, "Artemis", "2026-03-05T00:00:00Z")
    # Début différent → clé différente.
    assert k1 != marad_sync._synthetic_sched_key(cm, "Anemos", "2026-04-05T00:00:00Z")
    # Record vide (aucun identifiant) → pas de clé (record ignoré en amont).
    assert marad_sync._synthetic_sched_key({}, None, None) is None


def test_sync_schedules_resolves_vessel_by_code(monkeypatch) -> None:
    """Le champ `vessel` peut porter le NUMÉRO Marad → match sur Vessel.code."""
    from app.models.crew import MaradCrewSchedule
    from app.models.vessel import Vessel

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _check(s):
        from sqlalchemy import select

        vessel = Vessel(code="CF", name="Anemos")
        s.add(vessel)
        await s.flush()
        # Le schedule référence le navire par "CF" (= notre code, pas le nom).
        monkeypatch.setattr(
            marad,
            "list_schedules",
            lambda modified_since=None: _ret([{"id": "sx", "vessel": "cf"}]),
        )
        await marad_sync.sync_schedules(s)
        row = (
            await s.execute(
                select(MaradCrewSchedule).where(MaradCrewSchedule.marad_schedule_id == "sx")
            )
        ).scalar_one()
        assert row.vessel_id == vessel.id

    _run_with_db(_check)


def test_sync_schedules_skips_without_id_and_handles_unmapped(monkeypatch) -> None:
    from app.models.crew import MaradCrewSchedule

    monkeypatch.setattr(marad, "enabled", lambda: True)
    # Un schedule sans id (skip) + un schedule sans réfs résolvables (navire inconnu).
    monkeypatch.setattr(
        marad,
        "list_schedules",
        lambda modified_since=None: _ret([{"rank": "Bosco"}, {"id": "s2", "vessel": "Inconnu"}]),
    )

    async def _check(s):
        from sqlalchemy import select

        r = await marad_sync.sync_schedules(s)
        assert (r["fetched"], r["created"], r["skipped"]) == (2, 1, 1)
        row = (
            await s.execute(
                select(MaradCrewSchedule).where(MaradCrewSchedule.marad_schedule_id == "s2")
            )
        ).scalar_one()
        # Réfs non résolues → NULL, mais la ligne miroir existe quand même.
        assert row.crew_member_id is None
        assert row.vessel_id is None
        assert row.leg_id is None
        assert row.marad_vessel_name == "Inconnu"
        assert row.marad_voyage_ref is None  # pas de port → pas de route

    _run_with_db(_check)


def test_sync_all_combines_crew_and_schedules(monkeypatch) -> None:
    monkeypatch.setattr(marad, "enabled", lambda: True)
    monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret([_crew_record()]))
    monkeypatch.setattr(
        marad,
        "list_schedules",
        lambda modified_since=None: _ret([{"id": "s1", "vessel": "X"}]),
    )

    async def _check(s):
        r = await marad_sync.sync_all(s)
        assert r["configured"] is True
        assert r["crew_created"] == 1
        assert r["sched_created"] == 1
        assert r["errors"] == 0
        assert r["crew"]["fetched"] == 1 and r["schedules"]["fetched"] == 1

    _run_with_db(_check)


def test_sync_all_noop_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(marad, "enabled", lambda: False)

    async def _check(s):
        r = await marad_sync.sync_all(s)
        assert r["configured"] is False
        assert r["crew_created"] == 0 and r["sched_created"] == 0

    _run_with_db(_check)


# ───────────────────────── Header d'auth (essai multi-candidats) ─────────────


class _FakeResp:
    def __init__(self, status: int, payload=None) -> None:
        self.status_code = status
        self._payload = payload
        self.content = b"x" if payload is not None else b""
        self.text = ""

    def json(self):
        return self._payload


class _FakeClient:
    """Faux httpx.AsyncClient : n'authentifie que pour ``accept_key``.

    ``accept_key`` est soit un nom de header (ex. ``ApiKey``, ``Authorization``),
    soit ``query:<param>`` (ex. ``query:apiKey``) pour l'auth en query string.
    """

    def __init__(self, accept_header: str, payload) -> None:
        self.accept_header = accept_header
        self.payload = payload
        self.tried: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _sig(self, headers, params) -> str:
        if headers:
            return next(iter(headers))  # nom du header (un seul par stratégie)
        if params:
            return "query:" + next(iter(params))
        return "?"

    async def request(self, method, url, params=None, json=None, headers=None):
        sig = self._sig(headers, params)
        self.tried.append(sig)
        if sig == self.accept_header:
            return _FakeResp(200, self.payload)
        return _FakeResp(403)


def test_request_probes_header_first(monkeypatch) -> None:
    """Sans header épinglé, on sonde les HEADERS d'abord (query string en repli).

    Marasoft a retiré l'auth par query string en v5.5.24 → le header X-Api-Key
    doit être l'essai #1 (et non plus « query:apikey » comme avant le correctif).
    """
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", None)  # non épinglé
    monkeypatch.setattr(marad, "_working_strategy", None)

    captured: dict = {}

    def _factory(*a, **k):
        c = _FakeClient(accept_header="X-Api-Key", payload=[{"ok": 1}])
        captured["client"] = c
        return c

    monkeypatch.setattr(marad.httpx, "AsyncClient", _factory)

    out = asyncio.run(marad.list_vessels())
    assert out == [{"ok": 1}]
    assert captured["client"].tried[0] == "X-Api-Key"  # header, essayé en 1er
    assert marad._working_strategy == "header:X-Api-Key"


def test_request_falls_through_to_header_apitoken(monkeypatch) -> None:
    """Si les headers usuels échouent, on tente les autres noms (ApiToken)."""
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", None)
    monkeypatch.setattr(marad, "_working_strategy", None)

    captured: dict = {}

    def _factory(*a, **k):
        c = _FakeClient(accept_header="ApiToken", payload=[{"ok": 1}])
        captured["client"] = c
        return c

    monkeypatch.setattr(marad.httpx, "AsyncClient", _factory)
    out = asyncio.run(marad.list_vessels())
    assert out == [{"ok": 1}]
    assert "ApiToken" in captured["client"].tried
    assert marad._working_strategy == "header:ApiToken"


def test_request_all_schemes_fail_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", None)
    monkeypatch.setattr(marad, "_working_strategy", None)
    monkeypatch.setattr(
        marad.httpx, "AsyncClient", lambda *a, **k: _FakeClient(accept_header="Nope", payload=None)
    )
    assert asyncio.run(marad.list_vessels()) is None
    assert marad._working_strategy is None


def test_request_tries_bearer_scheme(monkeypatch) -> None:
    """Le schéma Authorization: Bearer fait partie des candidats essayés."""
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", None)
    monkeypatch.setattr(marad, "_working_strategy", None)

    captured: dict = {}

    def _factory(*a, **k):
        c = _FakeClient(accept_header="Authorization", payload=[{"ok": 1}])
        captured["client"] = c
        return c

    monkeypatch.setattr(marad.httpx, "AsyncClient", _factory)
    out = asyncio.run(marad.list_vessels())
    assert out == [{"ok": 1}]
    assert "Authorization" in captured["client"].tried
    assert marad._working_strategy == "Authorization:Bearer"


def test_auth_strategies_respects_explicit_pin(monkeypatch) -> None:
    monkeypatch.setattr(marad, "_working_strategy", None)
    monkeypatch.setattr(marad.settings, "marad_api_key_header", "MyKey")
    strategies = marad._auth_strategies()
    labels = [label for label, _h, _p in strategies]
    assert labels[0] == "header:MyKey"  # pin .env prioritaire (header)
    assert "query:apikey" in labels and "header:ApiKey" in labels


def test_explicit_pin_is_single_shot_even_at_default(monkeypatch) -> None:
    """RC-1/RC-2 : un header épinglé (même « X-Api-Key ») est essayé SEUL.

    Un seul appel HTTP → pas de cascade 401→429 sur les endpoints à 1 req/min.
    Et le défaut peut enfin être forcé (avant, X-Api-Key n'était jamais épinglé).
    """
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", "X-Api-Key")
    monkeypatch.setattr(marad, "_working_strategy", None)

    captured: dict = {}

    def _factory(*a, **k):
        # Le faux serveur n'accepte PAS X-Api-Key → l'appel doit échouer sans
        # essayer d'autres schémas (single-shot).
        c = _FakeClient(accept_header="ApiToken", payload=[{"ok": 1}])
        captured["client"] = c
        return c

    monkeypatch.setattr(marad.httpx, "AsyncClient", _factory)
    out = asyncio.run(marad.list_vessels())
    assert out is None  # pinné sur X-Api-Key qui échoue → pas de repli
    assert captured["client"].tried == ["X-Api-Key"]  # un seul essai, pas de cascade


def test_memorized_strategy_is_single_shot(monkeypatch) -> None:
    """Un schéma déjà mémorisé est réutilisé seul (économie de quota 1 req/min)."""
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", None)
    monkeypatch.setattr(marad, "_working_strategy", "header:ApiKey")

    captured: dict = {}

    def _factory(*a, **k):
        c = _FakeClient(accept_header="ApiKey", payload=[{"ok": 1}])
        captured["client"] = c
        return c

    monkeypatch.setattr(marad.httpx, "AsyncClient", _factory)
    out = asyncio.run(marad.list_vessels())
    assert out == [{"ok": 1}]
    assert captured["client"].tried == ["ApiKey"]  # un seul essai (mémorisé)


def test_prime_auth_noop_when_header_pinned(monkeypatch) -> None:
    """Header épinglé → prime_auth n'appelle PAS getVessels (un appel économisé).

    Il n'y a rien à découvrir (un seul schéma sera essayé partout), et chaque
    appel compte quand la clé est partagée avec d'autres consommateurs.
    """
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", "ApiKey")
    monkeypatch.setattr(marad, "_working_strategy", None)

    def _no_http(*a, **k):
        raise AssertionError("prime_auth ne doit faire AUCUN appel HTTP (header épinglé)")

    monkeypatch.setattr(marad.httpx, "AsyncClient", _no_http)
    assert asyncio.run(marad.prime_auth()) is None


class _Fake429Client:
    """Faux httpx.AsyncClient : renvoie systématiquement 429."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, params=None, json=None, headers=None):
        return _FakeResp(429)


def test_request_records_last_status_on_429(monkeypatch) -> None:
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", "ApiKey")
    monkeypatch.setattr(marad, "_working_strategy", None)
    monkeypatch.setattr(marad, "_last_status", {})
    monkeypatch.setattr(marad.httpx, "AsyncClient", lambda *a, **k: _Fake429Client())

    assert asyncio.run(marad.list_crew()) is None
    assert marad.last_status("/api/Crewing") == 429


def test_sync_all_quota_diagnostic_without_probing(monkeypatch) -> None:
    """429 sur les endpoints crew → diagnostic « quota » direct, SANS re-sonder
    getVessels (diagnose ne doit pas être appelé : chaque appel coûte du quota)."""
    monkeypatch.setattr(marad, "enabled", lambda: True)
    monkeypatch.setattr(marad.settings, "marad_api_key_header", "ApiKey")
    monkeypatch.setattr(marad, "_working_strategy", None)
    monkeypatch.setattr(marad, "list_crew", lambda modified_since=None: _ret(None))
    monkeypatch.setattr(marad, "list_schedules", lambda modified_since=None: _ret(None))
    monkeypatch.setattr(marad, "last_status", lambda path: 429)

    probed: list[str] = []

    async def _diagnose():
        probed.append("diagnose")
        return {}

    monkeypatch.setattr(marad, "diagnose", _diagnose)

    async def _check(s):
        r = await marad_sync.sync_all(s)
        assert r["crew_fetched"] == 0 and r["sched_fetched"] == 0
        assert "Quota Marad atteint" in r["diagnostic"]
        assert "/api/Crewing et /api/CrewingSchedule" in r["diagnostic"]
        assert probed == []  # pas d'appel getVessels supplémentaire

    _run_with_db(_check)


def test_rate_limited_endpoint_single_shot_when_auth_not_primed(monkeypatch) -> None:
    """Sur un endpoint à 1 req/min non authentifié, on ne sonde qu'UN schéma
    (sinon 7×403→429 brûleraient le quota de la minute). getVessels (15 req/min)
    garde la cascade complète."""
    monkeypatch.setattr(marad.settings, "marad_api_token", "secret-key")
    monkeypatch.setattr(marad.settings, "marad_api_key_header", "")
    marad._working_strategy = None  # auth pas encore amorcée

    # Tout échoue (403) → on mesure le nombre d'essais effectués.
    crew_client = _FakeClient(accept_header="Nope", payload=None)
    monkeypatch.setattr(marad.httpx, "AsyncClient", lambda *a, **k: crew_client)
    assert asyncio.run(marad.list_crew()) is None
    assert len(crew_client.tried) == 1  # /api/Crewing : un seul essai

    marad._working_strategy = None
    vessels_client = _FakeClient(accept_header="Nope", payload=None)
    monkeypatch.setattr(marad.httpx, "AsyncClient", lambda *a, **k: vessels_client)
    assert asyncio.run(marad.list_vessels()) is None
    assert len(vessels_client.tried) > 1  # getVessels : cascade complète autorisée
