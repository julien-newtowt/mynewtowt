"""Durcissement planification (audit 2026-07) — parcours DB complets.

Couvre : renumérotation chronologique des leg_codes, validation sur l'état
final simulé (cascade), cascade d'extension d'ETA, legs annulés hors
chevauchement, ETA-shift capitaine (tz + cascade + historisation),
drag-drop Gantt réel, verrou optimiste.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.port import Port
from app.models.schedule_revision import ScheduleRevision
from app.models.vessel import Vessel
from app.services.planning import (
    LegOverlap,
    create_leg,
    ensure_utc,
    update_leg,
)


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    state = SimpleNamespace(csrf_token="t", lang="fr")
    query_params: dict[str, str] = {}
    scope: dict = {"type": "http"}


async def _seed(db):
    db.add(Vessel(id=1, code="1", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    db.add(Port(id=3, locode="USNYC", name="New York", country="US"))
    await db.flush()


BASE = datetime(2026, 3, 1, tzinfo=UTC)


# ───────────────────── leg_code : rang chronologique ─────────────────────


@pytest.mark.asyncio
async def test_create_renumbers_by_etd_position(db):
    """Le rang (lettre) reflète l'ordre chronologique, pas l'ordre de création."""
    await _seed(db)
    # Créé EN PREMIER mais navigue en mars.
    leg_mar = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    assert leg_mar.leg_code == "1AFRBR6"
    # Créé ensuite mais navigue en janvier (avant) → il prend le rang A,
    # le leg de mars est renuméroté en B.
    leg_jan = await create_leg(
        db, vessel_id=1, departure_port_id=2, arrival_port_id=1,
        etd=datetime(2026, 1, 5, tzinfo=UTC), eta=datetime(2026, 1, 25, tzinfo=UTC),
    )
    assert leg_jan.leg_code == "1ABRFR6"
    await db.refresh(leg_mar)
    assert leg_mar.leg_code == "1BFRBR6"


@pytest.mark.asyncio
async def test_delete_renumbers_following_legs(db):
    from app.services.planning import delete_leg

    await _seed(db)
    leg_a = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=datetime(2026, 1, 5, tzinfo=UTC), eta=datetime(2026, 1, 25, tzinfo=UTC),
    )
    leg_b = await create_leg(
        db, vessel_id=1, departure_port_id=2, arrival_port_id=1,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    assert (leg_a.leg_code, leg_b.leg_code) == ("1AFRBR6", "1BBRFR6")
    await delete_leg(db, leg_a)
    await db.refresh(leg_b)
    assert leg_b.leg_code == "1ABRFR6"  # redevient le 1er de l'année


# ─────────────── validation sur l'état final (cascade simulée) ───────────────


@pytest.mark.asyncio
async def test_forward_shift_beyond_gap_is_allowed_with_cascade(db):
    """Avant : LegOverlap dès que le décalage dépassait l'interstice avec le
    leg suivant — alors même que la cascade l'aurait résolu. Désormais la
    validation porte sur l'état final simulé.
    """
    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    leg2 = await create_leg(
        db, vessel_id=1, departure_port_id=2, arrival_port_id=1,
        etd=BASE + timedelta(days=25), eta=BASE + timedelta(days=45),
    )
    # +10 j : la nouvelle ETA (J+30) mord sur l'ETD de leg2 (J+25).
    report = await update_leg(
        db, leg1,
        etd=BASE + timedelta(days=10), eta=BASE + timedelta(days=30),
        cascade=True,
    )
    assert report is not None and leg2.id in report.impacted_leg_ids
    await db.refresh(leg2)
    assert ensure_utc(leg2.etd) == BASE + timedelta(days=35)  # décalé de +10 j
    assert ensure_utc(leg2.eta) == BASE + timedelta(days=55)


@pytest.mark.asyncio
async def test_eta_extension_cascades_downstream(db):
    """Un pur allongement d'ETA (retard de transit) recale le leg suivant au
    lieu d'être refusé (comportement aligné sur le moteur scénario)."""
    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    leg2 = await create_leg(
        db, vessel_id=1, departure_port_id=2, arrival_port_id=1,
        etd=BASE + timedelta(days=22), eta=BASE + timedelta(days=42),
    )
    await update_leg(db, leg1, eta=BASE + timedelta(days=25), cascade=True)
    await db.refresh(leg2)
    assert ensure_utc(leg2.etd) == BASE + timedelta(days=25)  # repoussé
    assert ensure_utc(leg2.eta) == BASE + timedelta(days=45)  # durée conservée


@pytest.mark.asyncio
async def test_shift_without_cascade_still_validates_strictly(db):
    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    await create_leg(
        db, vessel_id=1, departure_port_id=2, arrival_port_id=1,
        etd=BASE + timedelta(days=25), eta=BASE + timedelta(days=45),
    )
    with pytest.raises(LegOverlap):
        await update_leg(db, leg1, eta=BASE + timedelta(days=30), cascade=False)


@pytest.mark.asyncio
async def test_cascade_shifts_booking_close_and_writes_revisions(db):
    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    leg2 = await create_leg(
        db, vessel_id=1, departure_port_id=2, arrival_port_id=1,
        etd=BASE + timedelta(days=25), eta=BASE + timedelta(days=45),
    )
    close_before = ensure_utc(leg2.booking_close_at)
    await update_leg(db, leg1, etd=BASE + timedelta(days=2), eta=BASE + timedelta(days=22))
    await db.refresh(leg2)
    # booking_close_at du leg aval suit le delta d'ETD (+2 j).
    assert ensure_utc(leg2.booking_close_at) == close_before + timedelta(days=2)
    # Historisation : une révision source (planning_edit) + une cascade.
    revs = list((await db.execute(select(ScheduleRevision))).scalars().all())
    sources = sorted(r.source for r in revs)
    assert sources == ["cascade", "planning_edit"]
    assert {r.batch_id for r in revs} == {revs[0].batch_id}  # même lot
    cascade_rev = next(r for r in revs if r.source == "cascade")
    assert cascade_rev.trigger_leg_id == leg1.id
    assert cascade_rev.leg_id == leg2.id


# ───────────────────── legs annulés hors chevauchement ─────────────────────


@pytest.mark.asyncio
async def test_cancelled_leg_frees_its_slot(db):
    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    leg1.status = "cancelled"
    await db.flush()
    # Même navire, même fenêtre : accepté car le leg annulé libère le créneau.
    replacement = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=3,
        etd=BASE + timedelta(hours=6), eta=BASE + timedelta(days=19),
    )
    assert replacement.leg_code == "1AFRUS6"  # rang A : l'annulé ne compte plus


# ───────────────── ETA-shift capitaine (tz + cascade + trace) ─────────────────


@pytest.mark.asyncio
async def test_eta_shift_parses_tz_and_cascades(db, staff_user):
    """Régression du bug production : la saisie naïve du formulaire mélangée
    à l'ETA aware levait TypeError, avalé par contextlib.suppress → la
    cascade et les notifications ne tournaient jamais.
    """
    from app.models.notification import Notification
    from app.models.sof_event import EtaShift
    from app.routers.captain_router import declare_eta_shift

    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    leg2 = await create_leg(
        db, vessel_id=1, departure_port_id=2, arrival_port_id=1,
        etd=BASE + timedelta(days=22), eta=BASE + timedelta(days=42),
    )
    resp = await declare_eta_shift(
        leg1.id,
        _Req(),
        new_eta="2026-03-26T00:00",  # +5 j — mord sur l'ETD du leg suivant
        reason="weather",
        detail="tempête",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    assert ensure_utc(leg1.eta) == BASE + timedelta(days=25)
    # Historique EtaShift complet.
    shift = (await db.execute(select(EtaShift))).scalar_one()
    assert ensure_utc(shift.previous_eta) == BASE + timedelta(days=20)
    assert ensure_utc(shift.new_eta) == BASE + timedelta(days=25)
    # La cascade a bien tourné : leg aval repoussé.
    await db.refresh(leg2)
    assert ensure_utc(leg2.etd) == BASE + timedelta(days=25)
    # Révisions : source eta_shift + cascade, même lot.
    revs = list((await db.execute(select(ScheduleRevision))).scalars().all())
    assert sorted(r.source for r in revs) == ["cascade", "eta_shift"]
    # Notification staff émise.
    notifs = list((await db.execute(select(Notification))).scalars().all())
    assert any(n.type == "eta_shift" for n in notifs)


@pytest.mark.asyncio
async def test_eta_shift_rejects_eta_before_departure(db, staff_user):
    from fastapi import HTTPException

    from app.routers.captain_router import declare_eta_shift

    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    with pytest.raises(HTTPException) as exc:
        await declare_eta_shift(
            leg1.id, _Req(), new_eta="2026-02-01T00:00", reason="weather",
            detail=None, db=db, user=staff_user,
        )
    assert exc.value.status_code == 400


# ───────────────────── drag-drop Gantt réel + verrou ─────────────────────


@pytest.mark.asyncio
async def test_gantt_move_endpoint_updates_and_cascades(db, staff_user):
    import json

    from app.routers.planning_router import move_leg_action

    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    resp = await move_leg_action(
        _Req(), leg1.id, etd="2026-03-03T00:00", eta="2026-03-23T00:00",
        db=db, user=staff_user,
    )
    payload = json.loads(resp.body)
    assert payload["ok"] is True
    assert ensure_utc(leg1.etd) == BASE + timedelta(days=2)
    rev = (await db.execute(select(ScheduleRevision))).scalars().first()
    assert rev is not None and rev.source == "gantt_move"


@pytest.mark.asyncio
async def test_gantt_move_refuses_sailed_leg(db, staff_user):
    import json

    from app.routers.planning_router import move_leg_action

    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    leg1.atd = BASE
    await db.flush()
    resp = await move_leg_action(
        _Req(), leg1.id, etd="2026-03-05T00:00", eta="2026-03-25T00:00",
        db=db, user=staff_user,
    )
    assert json.loads(resp.body)["ok"] is False


@pytest.mark.asyncio
async def test_optimistic_lock_rejects_stale_edit(db, staff_user):
    from app.routers.planning_router import update_leg_action
    from tests.integration.conftest import FakeRequest

    await _seed(db)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=BASE, eta=BASE + timedelta(days=20),
    )
    stale = "2020-01-01T00:00:00+00:00"  # updated_at périmé
    req = FakeRequest(
        form={
            "etd": "2026-03-02T00:00",
            "eta": "2026-03-22T00:00",
            "cascade": "on",
            "expected_updated_at": stale,
        }
    )
    # Attributs requis par le rendu du formulaire (context processor i18n + layout).
    req.state = SimpleNamespace(csrf_token="t", lang="fr")
    req.cookies = {}
    req.query_params = {}
    req.scope = {"type": "http"}
    req.url = SimpleNamespace(path="/planning/legs/1/edit", query="")
    resp = await update_leg_action(req, leg1.id, db=db, user=staff_user)
    assert resp.status_code == 409
    # Le leg n'a PAS été modifié.
    assert ensure_utc(leg1.etd) == BASE


# ───────────── cascade sur changement d'année → renumérotation ─────────────


@pytest.mark.asyncio
async def test_cascade_crossing_year_renumbers_leg_code(db):
    await _seed(db)
    dec = datetime(2026, 12, 1, tzinfo=UTC)
    leg1 = await create_leg(
        db, vessel_id=1, departure_port_id=1, arrival_port_id=2,
        etd=dec, eta=dec + timedelta(days=18),
    )
    leg2 = await create_leg(
        db, vessel_id=1, departure_port_id=2, arrival_port_id=1,
        etd=dec + timedelta(days=22), eta=dec + timedelta(days=40),
    )
    assert leg2.leg_code == "1BBRFR6"
    # +15 j sur leg1 → leg2 part le 7 janvier 2027 : il devient le 1er leg
    # de 2027 (rang A) avec le chiffre d'année 7.
    await update_leg(
        db, leg1, etd=dec + timedelta(days=15), eta=dec + timedelta(days=33), cascade=True
    )
    await db.refresh(leg2)
    assert ensure_utc(leg2.etd).year == 2027
    assert leg2.leg_code == "1ABRFR7"
