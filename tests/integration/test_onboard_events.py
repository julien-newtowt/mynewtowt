"""LOT 4 — Saisie bord déclarative des événements MRV : tests d'intégration.

Patron ``tests/integration/test_bunkering_screens.py`` (coroutines de route
appelées directement, hors ASGI, avec ``db``/``FakeRequest`` de
``tests/integration/conftest.py``). Couvre : gate ``captain:M`` (403 sinon),
wizard POST → brouillon, reprise auteur-seul (403), autosave (204 + maj),
finalisation OK (statut + QualityCheckResults), finalisation incomplète
(200 + messages, pas 500), position manuelle sans justification (refus R05),
landing affichant les brouillons, cron R19 (503/403/OK).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.leg import Leg
from app.models.nav_event import NavEvent, NoonEvent
from app.models.port import Port
from app.models.user import User
from app.models.validation import QualityCheckResult
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import referential_env
from app.services.validation_engine import invalidate_cache, seed_reference_data
from tests.integration.conftest import FakeRequest


async def _captain(db, user_id: int = 10, assigned_vessel_id: int | None = None) -> User:
    u = User(
        id=user_id, username=f"cdt{user_id}", email=f"cdt{user_id}@ex.test",
        hashed_password="x", full_name="Cdt Test", role="manager_maritime",
        assigned_vessel_id=assigned_vessel_id,
    )
    db.add(u)
    await db.flush()
    return u


async def _vessel_with_engines(db, code: str = "ANE") -> Vessel:
    v = Vessel(code=code, name="Anemos")
    db.add(v)
    await db.flush()
    await referential_env.ensure_vessel_env_defaults(db, v)  # 6 moteurs + 5 cuves
    return v


async def _leg(db, vessel: Vessel, *, active: bool = True, leg_code: str = "1AFRBR6") -> Leg:
    pol = Port(locode="FRFEC", name="Fécamp", country="FR")
    pod = Port(locode="BRSSZ", name="Santos", country="BR")
    db.add_all([pol, pod])
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(
        leg_code=leg_code, vessel_id=vessel.id,
        departure_port_id=pol.id, arrival_port_id=pod.id,
        etd=base, eta=base + timedelta(days=20), etd_ref=base, eta_ref=base + timedelta(days=20),
        atd=(base if active else None), ata=None,
    )
    db.add(leg)
    await db.flush()
    # Catalogue de règles seedé : la finalisation persiste des QualityCheckResult
    # dont ``rule_id`` est une FK vers ``validation_rules`` (enforced sous SQLite).
    invalidate_cache()
    await seed_reference_data(db)
    invalidate_cache()
    return leg


def _noon_form(leg_id, **extra):
    f = {
        "event_type": "noon",
        "leg_id": str(leg_id),
        "datetime_local": "2026-04-02T12:00",
        "timezone": "UTC",
        "lat_decimal": "48.5",
        "lon_decimal": "-5.1",
        "position_source": "thalos_auto",
    }
    f.update(extra)
    return f


# ─────────────────────────── routes enregistrées ───────────────────────────


def test_event_routes_registered():
    from app.routers import onboard_router

    paths = {r.path for r in onboard_router.router.routes}
    for p in (
        "/onboard/events",
        "/onboard/events/new/{event_type}",
        "/onboard/events/{event_id}/edit",
        "/onboard/events/{event_id}/autosave",
        "/onboard/events/{event_id}/finalize",
    ):
        assert p in paths
    api_paths = {r.path for r in onboard_router.api_router.routes}
    assert "/api/mrv/draft-reminders" in api_paths


# ════════════════════════════════════ Gate de permission captain:M


@pytest.mark.asyncio
async def test_events_require_captain_m(db):
    checker = require_permission("captain", "M")
    commercial = SimpleNamespace(id=99, full_name="Com", username="com", role="commercial")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=commercial, db=db)
    assert exc.value.status_code == 403

    captain = SimpleNamespace(id=11, full_name="Cdt", username="cdt", role="manager_maritime")
    assert await checker(FakeRequest(), user=captain, db=db) is captain


# ════════════════════════════════════ Wizard + création brouillon


@pytest.mark.asyncio
async def test_new_form_renders(db):
    from app.routers.onboard_router import onboard_event_new_form

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    user = await _captain(db, assigned_vessel_id=v.id)
    resp = await onboard_event_new_form(
        "noon", FakeRequest(), vessel_id=v.id, leg_id=leg.id, db=db, user=user
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/onboard/event_form.html"
    assert resp.context["event"] is None
    assert len(resp.context["engines"]) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("etype", ["departure", "arrival", "anchoring_begin", "anchoring_end"])
async def test_new_form_renders_all_types(db, etype):
    """Le wizard rend chaque type (branches portcall/anchoring du gabarit)."""
    from app.routers.onboard_router import onboard_event_new_form

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    user = await _captain(db, assigned_vessel_id=v.id)
    resp = await onboard_event_new_form(
        etype, FakeRequest(), vessel_id=v.id, leg_id=leg.id, db=db, user=user
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/onboard/event_form.html"
    assert resp.context["event_type"] == etype


@pytest.mark.asyncio
async def test_create_departure_draft(db):
    """Un Departure se crée avec ses champs escale (ROB de référence, condition)."""
    from app.routers.onboard_router import onboard_event_create

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    user = await _captain(db, assigned_vessel_id=v.id)
    form = {
        "event_type": "departure", "leg_id": str(leg.id),
        "datetime_local": "2026-04-01T06:00", "timezone": "UTC",
        "lat_decimal": "49.76", "lon_decimal": "0.37", "position_source": "thalos_auto",
        "vessel_condition": "laden", "rob_t": "42.5", "cargo_bl_t": "900",
    }
    resp = await onboard_event_create(FakeRequest(form), db=db, user=user)
    assert resp.status_code == 303
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])
    ev = await db.get(NavEvent, event_id)
    assert ev.event_type == "departure"
    assert str(ev.rob_t) == "42.500"
    assert ev.vessel_condition == "laden"


@pytest.mark.asyncio
async def test_new_form_unknown_type_404(db):
    from app.routers.onboard_router import onboard_event_new_form

    user = await _captain(db)
    with pytest.raises(HTTPException) as exc:
        await onboard_event_new_form("bogus", FakeRequest(), db=db, user=user)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_makes_draft(db):
    from app.routers.onboard_router import onboard_event_create

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    user = await _captain(db, assigned_vessel_id=v.id)
    resp = await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=user)
    assert resp.status_code == 303
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])  # .../{id}/edit

    ev = await db.get(NavEvent, event_id)
    assert ev is not None
    assert ev.status == "brouillon"
    assert ev.event_type == "noon"
    assert ev.author_user_id == user.id
    assert ev.vessel_id == v.id
    assert ev.datetime_utc is not None  # local + tz calculé


@pytest.mark.asyncio
async def test_create_with_engine_readings(db):
    from app.routers.onboard_router import onboard_event_create

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    user = await _captain(db, assigned_vessel_id=v.id)
    engines = await referential_env.get_vessel_engines(db, v.id)
    e0 = engines[0]
    form = _noon_form(leg.id, **{f"eng_hours_{e0.id}": "1200.5", f"eng_fuel_{e0.id}": "34000"})
    resp = await onboard_event_create(FakeRequest(form), db=db, user=user)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])
    ev = await db.get(NoonEvent, event_id)
    assert len(ev.engine_readings) == 1
    assert ev.engine_readings[0].engine_id == e0.id


# ════════════════════════════════════ Reprise / garde auteur-seul


@pytest.mark.asyncio
async def test_edit_rejects_non_author(db):
    from app.routers.onboard_router import onboard_event_create, onboard_event_edit_post

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, user_id=10, assigned_vessel_id=v.id)
    resp = await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=author)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])

    other = await _captain(db, user_id=20, assigned_vessel_id=v.id)
    with pytest.raises(HTTPException) as exc:
        await onboard_event_edit_post(
            event_id, FakeRequest(_noon_form(leg.id, comments="hijack")), db=db, user=other
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_edit_form_rejects_non_author(db):
    from app.routers.onboard_router import onboard_event_create, onboard_event_edit_form

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, user_id=10, assigned_vessel_id=v.id)
    resp = await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=author)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])

    other = await _captain(db, user_id=21, assigned_vessel_id=v.id)
    with pytest.raises(HTTPException) as exc:
        await onboard_event_edit_form(event_id, FakeRequest(), db=db, user=other)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_edit_allows_author(db):
    from app.routers.onboard_router import onboard_event_create, onboard_event_edit_post

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, assigned_vessel_id=v.id)
    resp = await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=author)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])

    resp2 = await onboard_event_edit_post(
        event_id, FakeRequest(_noon_form(leg.id, comments="RAS bonne mer")), db=db, user=author
    )
    assert resp2.status_code == 303
    ev = await db.get(NoonEvent, event_id)
    assert ev.comments == "RAS bonne mer"


# ════════════════════════════════════ Autosave


@pytest.mark.asyncio
async def test_autosave_updates_draft(db):
    from app.routers.onboard_router import onboard_event_autosave, onboard_event_create

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, assigned_vessel_id=v.id)
    resp = await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=author)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])
    ev = await db.get(NoonEvent, event_id)
    saved_before = ev.last_saved_at

    r = await onboard_event_autosave(
        event_id, FakeRequest(_noon_form(leg.id, comments="autosaved")), db=db, user=author
    )
    assert r.status_code == 204
    await db.refresh(ev)
    assert ev.comments == "autosaved"
    assert ev.last_saved_at is not None and ev.last_saved_at >= saved_before


@pytest.mark.asyncio
async def test_autosave_rejects_non_author(db):
    from app.routers.onboard_router import onboard_event_autosave, onboard_event_create

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, user_id=10, assigned_vessel_id=v.id)
    resp = await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=author)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])

    other = await _captain(db, user_id=22, assigned_vessel_id=v.id)
    r = await onboard_event_autosave(
        event_id, FakeRequest(_noon_form(leg.id, comments="x")), db=db, user=other
    )
    assert r.status_code == 403


# ════════════════════════════════════ Finalisation


@pytest.mark.asyncio
async def test_finalize_ok(db):
    from app.routers.onboard_router import onboard_event_create, onboard_event_finalize

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, assigned_vessel_id=v.id)
    resp = await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=author)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])

    r = await onboard_event_finalize(
        event_id, FakeRequest(_noon_form(leg.id)), db=db, user=author
    )
    assert r.status_code == 303  # succès → redirection liste
    ev = await db.get(NavEvent, event_id)
    assert ev.status == "finalise"
    assert ev.finalized_at is not None
    qcr = (
        await db.execute(
            select(func.count()).select_from(QualityCheckResult).where(
                QualityCheckResult.subject_id == event_id
            )
        )
    ).scalar_one()
    assert qcr > 0  # le moteur de règles a persisté ses verdicts


@pytest.mark.asyncio
async def test_finalize_incomplete_shows_errors_not_500(db):
    from app.routers.onboard_router import onboard_event_create, onboard_event_finalize

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, assigned_vessel_id=v.id)
    # brouillon SANS date/heure → R01 (date manquante, bloquant).
    incomplete = {
        "event_type": "noon", "leg_id": str(leg.id),
        "datetime_local": "", "timezone": "UTC", "position_source": "thalos_auto",
    }
    resp = await onboard_event_create(FakeRequest(dict(incomplete)), db=db, user=author)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])

    r = await onboard_event_finalize(event_id, FakeRequest(dict(incomplete)), db=db, user=author)
    assert r.status_code == 200  # réaffichage du formulaire, pas 500
    assert r.template.name == "staff/onboard/event_form.html"
    assert r.context["errors"]  # messages de règles présents
    ev = await db.get(NavEvent, event_id)
    assert ev.status == "brouillon"  # non finalisé


@pytest.mark.asyncio
async def test_finalize_manual_position_without_justification_refused(db):
    from app.routers.onboard_router import onboard_event_create, onboard_event_finalize

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, assigned_vessel_id=v.id)
    form = _noon_form(leg.id, position_source="manuel_justifie")  # lat/lon présents, pas de justif
    resp = await onboard_event_create(FakeRequest(dict(form)), db=db, user=author)
    event_id = int(resp.headers["location"].rstrip("/").split("/")[-2])

    r = await onboard_event_finalize(event_id, FakeRequest(dict(form)), db=db, user=author)
    assert r.status_code == 200
    assert any("R05" in m for m in r.context["errors"])
    ev = await db.get(NavEvent, event_id)
    assert ev.status == "brouillon"

    # Avec justification → finalisation acceptée.
    ok_form = _noon_form(
        leg.id, position_source="manuel_justifie", position_justification="Thalos HS, point sextant"
    )
    r2 = await onboard_event_finalize(event_id, FakeRequest(ok_form), db=db, user=author)
    assert r2.status_code == 303
    await db.refresh(ev)
    assert ev.status == "finalise"


# ════════════════════════════════════ Liste + landing


@pytest.mark.asyncio
async def test_events_index_renders(db):
    from app.routers.onboard_router import onboard_event_create, onboard_events_index

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, assigned_vessel_id=v.id)
    await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=author)

    resp = await onboard_events_index(FakeRequest(), leg_id=leg.id, db=db, user=author)
    assert resp.status_code == 200
    assert resp.template.name == "staff/onboard/events_list.html"
    assert len(resp.context["events"]) == 1
    assert len(resp.context["my_drafts"]) == 1


@pytest.mark.asyncio
async def test_landing_shows_drafts(db):
    from app.routers.onboard_router import onboard_event_create, onboard_landing

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, assigned_vessel_id=v.id)
    await onboard_event_create(FakeRequest(_noon_form(leg.id)), db=db, user=author)

    resp = await onboard_landing(FakeRequest(), db=db, user=author)
    assert resp.status_code == 200
    assert len(resp.context["my_drafts"]) == 1


# ════════════════════════════════════ Offline — idempotence rejeu NoonEvent


@pytest.mark.asyncio
async def test_noon_event_replay_is_idempotent(db):
    from app.routers.onboard_router import onboard_event_create

    v = await _vessel_with_engines(db)
    leg = await _leg(db, v)
    author = await _captain(db, assigned_vessel_id=v.id)
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    r1 = await onboard_event_create(
        FakeRequest(_noon_form(leg.id, client_uuid=uuid)), db=db, user=author
    )
    r2 = await onboard_event_create(
        FakeRequest(_noon_form(leg.id, client_uuid=uuid)), db=db, user=author
    )
    assert r1.status_code == 303 and r2.status_code == 303
    count = (
        await db.execute(
            select(func.count()).select_from(NavEvent).where(NavEvent.client_uuid == uuid)
        )
    ).scalar_one()
    assert count == 1  # le rejeu offline n'a pas dupliqué (lot 3 dédoublonne)


# ════════════════════════════════════ Cron R19 — auth


@pytest.mark.asyncio
async def test_draft_reminders_cron_503_without_token(db, monkeypatch):
    from app.routers.onboard_router import mrv_draft_reminders_cron
    from app.routers import onboard_router

    monkeypatch.setattr(onboard_router.settings, "mrv_drafts_api_token", None)
    with pytest.raises(HTTPException) as exc:
        await mrv_draft_reminders_cron(FakeRequest(), db=db)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_draft_reminders_cron_rejects_bad_token(db, monkeypatch):
    from app.routers.onboard_router import mrv_draft_reminders_cron
    from app.routers import onboard_router

    monkeypatch.setattr(onboard_router.settings, "mrv_drafts_api_token", "s3cret")
    req = FakeRequest()
    req.headers = {"x-api-token": "wrong"}
    with pytest.raises(HTTPException) as exc:
        await mrv_draft_reminders_cron(req, db=db)
    assert exc.value.status_code == 403  # convention repo (crons existants : 403)


@pytest.mark.asyncio
async def test_draft_reminders_cron_ok_with_token(db, monkeypatch):
    from app.routers.onboard_router import mrv_draft_reminders_cron
    from app.routers import onboard_router

    monkeypatch.setattr(onboard_router.settings, "mrv_drafts_api_token", "s3cret")
    req = FakeRequest()
    req.headers = {"x-api-token": "s3cret"}
    resp = await mrv_draft_reminders_cron(req, db=db)
    assert resp.status_code == 200
