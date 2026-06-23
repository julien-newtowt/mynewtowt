"""Escale P0 — reprise (ESC-01/03/05) : tests d'intégration.

Couvre l'édition/suppression d'opérations et de shifts dockers, la saisie
manuelle des heures réelles et les propriétés de cadence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.crew import CrewAssignment, CrewMember
from app.models.escale import DockerShift, EscaleOperation
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(
        id=1,
        leg_code="1CFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    db.add(leg)
    await db.flush()
    return leg


# ─────────────────────────────── ESC-05 ───────────────────────────────


def test_docker_shift_rates():
    base = datetime(2026, 4, 1, tzinfo=UTC)
    s = DockerShift(
        leg_id=1,
        palettes_target=80,
        palettes_done=100,
        planned_start=base,
        planned_end=base + timedelta(hours=8),  # 80/8 = 10 pal/h
        actual_start=base,
        actual_end=base + timedelta(hours=8),  # 100/8 = 12.5 pal/h
    )
    assert s.planned_rate == 10.0
    assert s.actual_rate == 12.5
    assert s.rate_delta_pct == 25.0


def test_docker_shift_rates_none_when_incomplete():
    s = DockerShift(leg_id=1, palettes_target=None, palettes_done=0)
    assert s.planned_rate is None
    assert s.actual_rate is None
    assert s.rate_delta_pct is None


# ─────────────────────────── ESC-01 / ESC-03 ───────────────────────────


@pytest.mark.asyncio
async def test_edit_operation_sets_manual_actual_times(db, staff_user):
    from app.routers.escale_router import edit_operation

    await _setup_leg(db)
    op = EscaleOperation(
        leg_id=1, operation_type="technique", action="inspection", status="planned"
    )
    db.add(op)
    await db.flush()

    resp = await edit_operation(
        op.id,
        _Req(),
        direction="BOTH",
        operation_type="technique",
        action="inspection",
        label="Inspection cale",
        intervenant=None,
        planned_start=None,
        planned_end=None,
        actual_start="2026-04-01T10:00:00",
        actual_end="2026-04-01T12:00:00",
        status=None,
        cost_forecast=None,
        cost_actual=None,
        notes=None,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    await db.refresh(op)
    assert op.actual_start is not None and op.actual_end is not None
    assert op.status == "completed"  # déduit de actual_end
    assert op.label == "Inspection cale"


@pytest.mark.asyncio
async def test_edit_operation_reconciles_sof_no_duplicate(db, staff_user):
    """Éditer l'heure d'une opération ne doit PAS accumuler de SOF obsolètes."""
    from app.models.sof_event import SofEvent
    from app.routers.escale_router import edit_operation

    await _setup_leg(db)
    op = EscaleOperation(leg_id=1, operation_type="relations_externes", action="nor")
    db.add(op)
    await db.flush()

    common = {
        "direction": "BOTH",
        "operation_type": "relations_externes",
        "action": "nor",
        "label": None,
        "intervenant": None,
        "planned_start": None,
        "planned_end": None,
        "status": None,
        "cost_forecast": None,
        "cost_actual": None,
        "notes": None,
        "db": db,
        "user": staff_user,
    }
    # 1re saisie : SOF NOR créé à 10:00.
    await edit_operation(
        op.id, _Req(), actual_start="2026-04-01T10:00:00", actual_end=None, **common
    )
    # Correction : l'heure passe à 11:00 → l'ancien SOF doit être remplacé.
    await edit_operation(
        op.id, _Req(), actual_start="2026-04-01T11:00:00", actual_end=None, **common
    )

    sofs = (
        await db.execute(SofEvent.__table__.select().where(SofEvent.__table__.c.leg_id == 1))
    ).fetchall()
    nor_events = [s for s in sofs if s.event_type == "NOR"]
    assert len(nor_events) == 1, f"SOF NOR dupliqués : {len(nor_events)}"
    assert nor_events[0].occurred_at.hour == 11


@pytest.mark.asyncio
async def test_delete_operation(db, staff_user):
    from app.routers.escale_router import delete_operation

    await _setup_leg(db)
    op = EscaleOperation(leg_id=1, operation_type="technique", action="inspection")
    db.add(op)
    await db.flush()
    oid = op.id
    resp = await delete_operation(oid, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(EscaleOperation, oid)) is None


@pytest.mark.asyncio
async def test_edit_operation_rejected_when_escale_locked(db, staff_user):
    from fastapi import HTTPException

    from app.routers.escale_router import edit_operation

    leg = await _setup_leg(db)
    leg.escale_locked_at = datetime.now(UTC)
    op = EscaleOperation(leg_id=1, operation_type="technique", action="inspection")
    db.add(op)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await edit_operation(
            op.id,
            _Req(),
            direction="BOTH",
            operation_type="technique",
            action="inspection",
            label=None,
            planned_start=None,
            planned_end=None,
            actual_start=None,
            actual_end=None,
            status=None,
            cost_forecast=None,
            cost_actual=None,
            notes=None,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_port_status_flow_ata_atd(db, staff_user):
    """ESC-02 : pose ATA puis ATD, recalcule la finance, garde l'ordre."""
    from fastapi import HTTPException

    from app.models.finance import LegFinance
    from app.routers.escale_router import update_port_status

    leg = await _setup_leg(db)

    # Départ refusé avant l'arrivée.
    with pytest.raises(HTTPException) as exc:
        await update_port_status(
            leg.id,
            _Req(),
            new_status="pilote_depart",
            status_time="2026-04-25T10:00:00",
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400

    # À quai → ATA posée, statut in_progress, finance recalculée (rollup).
    resp = await update_port_status(
        leg.id,
        _Req(),
        new_status="a_quai",
        status_time="2026-04-21T10:00:00",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    # ``leg`` est identity-mappé : la route a muté le même objet (pas de refresh
    # — qui relirait des datetimes naïfs sous SQLite et fausserait le test).
    assert leg.ata is not None
    assert leg.status == "in_progress"
    fin = (
        await db.execute(
            LegFinance.__table__.select().where(LegFinance.__table__.c.leg_id == leg.id)
        )
    ).fetchone()
    assert fin is not None  # rollup_for_leg a créé la ligne finance

    # Pilote départ → ATD posée, statut completed.
    resp = await update_port_status(
        leg.id,
        _Req(),
        new_status="pilote_depart",
        status_time="2026-04-25T10:00:00",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    assert leg.atd is not None
    assert leg.status == "completed"


@pytest.mark.asyncio
async def test_port_status_notifications_are_idempotent(db, staff_user):
    """ESC-02 : re-soumettre (correction d'horodatage) ne réémet PAS de
    notification — une seule EOSP et une seule SOSP par escale.
    """
    from app.models.notification import Notification
    from app.routers.escale_router import update_port_status

    leg = await _setup_leg(db)

    async def _count(notif_type: str) -> int:
        rows = (
            await db.execute(
                Notification.__table__.select().where(Notification.__table__.c.type == notif_type)
            )
        ).fetchall()
        return len(rows)

    # 1re arrivée → 1 EOSP.
    await update_port_status(
        leg.id,
        _Req(),
        new_status="a_quai",
        status_time="2026-04-21T10:00:00",
        db=db,
        user=staff_user,
    )
    assert await _count("eosp") == 1
    # Correction de l'horodatage d'arrivée → toujours 1 EOSP (pas de doublon).
    await update_port_status(
        leg.id,
        _Req(),
        new_status="a_quai",
        status_time="2026-04-21T11:00:00",
        db=db,
        user=staff_user,
    )
    assert leg.ata.hour == 11  # la correction est bien appliquée
    assert await _count("eosp") == 1

    # 1er départ → 1 SOSP ; re-soumission → toujours 1.
    await update_port_status(
        leg.id,
        _Req(),
        new_status="pilote_depart",
        status_time="2026-04-25T10:00:00",
        db=db,
        user=staff_user,
    )
    await update_port_status(
        leg.id,
        _Req(),
        new_status="pilote_depart",
        status_time="2026-04-25T12:00:00",
        db=db,
        user=staff_user,
    )
    assert await _count("sosp") == 1


@pytest.mark.asyncio
async def test_edit_and_delete_docker_shift(db, staff_user):
    from app.routers.escale_router import delete_docker_shift, edit_docker_shift

    await _setup_leg(db)
    s = DockerShift(leg_id=1, company="Dockers SA", nb_dockers=4)
    db.add(s)
    await db.flush()

    resp = await edit_docker_shift(
        s.id,
        _Req(),
        direction="BOTH",
        company="Dockers Atlantique",
        nb_dockers=6,
        palettes_target=80,
        palettes_done=40,
        hold="AR",
        planned_start=None,
        planned_end=None,
        actual_start="2026-04-01T08:00:00",
        actual_end="2026-04-01T16:00:00",
        cost_eur=None,
        notes=None,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    await db.refresh(s)
    assert s.company == "Dockers Atlantique"
    assert s.nb_dockers == 6
    assert s.palettes_done == 40
    assert s.hold == "AR"

    sid = s.id
    resp = await delete_docker_shift(sid, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(DockerShift, sid)) is None


# ─────────────────────────────── ESC-04 ───────────────────────────────


@pytest.mark.asyncio
async def test_create_operation_persists_intervenant(db, staff_user):
    from app.routers.escale_router import create_operation

    await _setup_leg(db)
    resp = await create_operation(
        1,
        _Req(),
        direction="IMPORT",
        operation_type="manutention",
        action="dechargement",
        label="Déchargement cale AR",
        intervenant="Manutentions du Port SARL",
        planned_start="2026-04-01T08:00:00",
        planned_end="2026-04-01T14:00:00",
        cost_forecast=None,
        cost_actual=None,
        notes=None,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    op = (await db.execute(EscaleOperation.__table__.select())).fetchone()
    assert op.intervenant == "Manutentions du Port SARL"


def test_operation_durations_computed():
    base = datetime(2026, 4, 1, tzinfo=UTC)
    op = EscaleOperation(
        leg_id=1,
        operation_type="manutention",
        action="dechargement",
        planned_start=base,
        planned_end=base + timedelta(hours=6),
        actual_start=base + timedelta(minutes=30),
        actual_end=base + timedelta(hours=5, minutes=30),
    )
    assert op.planned_duration_hours == 6.0
    assert op.actual_duration_hours == 5.0


def test_operation_durations_none_when_incomplete():
    op = EscaleOperation(leg_id=1, operation_type="t", action="a", planned_start=None)
    assert op.planned_duration_hours is None
    assert op.actual_duration_hours is None


# ─────────────────────────────── ESC-06 ───────────────────────────────


@pytest.mark.asyncio
async def test_embarkation_creates_assignment_and_paf(db, staff_user):
    """Saisir un embarquement avec un marin crée l'affectation + le passage PAF (FR)."""
    from app.routers.escale_router import create_operation

    await _setup_leg(db)  # POL = FRFEC (France)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()

    resp = await create_operation(
        1,
        _Req(),
        direction="BOTH",
        operation_type="armement",
        action="embarquement",
        label="Embarquement relève",
        intervenant=None,
        crew_member_id=m.id,
        planned_start="2026-04-01T08:00:00",
        planned_end=None,
        cost_forecast=None,
        cost_actual=None,
        notes=None,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    # CrewAssignment créée pour ce marin / navire.
    assignments = (await db.execute(CrewAssignment.__table__.select())).fetchall()
    assert len(assignments) == 1
    assert assignments[0].crew_member_id == m.id
    assert assignments[0].vessel_id == 1
    assert assignments[0].embark_port_id == 1  # FRFEC
    # Passage PAF auto (FRFEC = port français).
    ops = (
        await db.execute(
            EscaleOperation.__table__.select().where(
                EscaleOperation.__table__.c.action == "passage_paf"
            )
        )
    ).fetchall()
    assert len(ops) == 1


@pytest.mark.asyncio
async def test_disembarkation_closes_active_assignment(db):
    from app.routers.escale_router import EscaleOperation as _Op
    from app.services.escale_crew import couple_crew_assignment

    leg = await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()
    db.add(
        CrewAssignment(
            crew_member_id=m.id,
            leg_id=1,
            vessel_id=1,
            embark_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
    )
    await db.flush()

    op = _Op(
        leg_id=1,
        operation_type="armement",
        action="debarquement",
        actual_start=datetime(2026, 4, 20, tzinfo=UTC),
    )
    db.add(op)
    await db.flush()
    closed = await couple_crew_assignment(db, op, leg, m.id)
    assert closed is not None
    assert closed.disembark_at is not None
    assert closed.disembark_port_id == 2  # port d'arrivée


@pytest.mark.asyncio
async def test_paf_idempotent_per_leg(db):
    from app.services.escale_crew import maybe_create_paf

    leg = await _setup_leg(db)
    op1 = EscaleOperation(leg_id=1, operation_type="armement", action="embarquement")
    op2 = EscaleOperation(leg_id=1, operation_type="armement", action="embarquement")
    db.add_all([op1, op2])
    await db.flush()
    assert await maybe_create_paf(db, op1, leg) is not None
    assert await maybe_create_paf(db, op2, leg) is None  # déjà un PAF sur le leg


def test_embarkation_alerts():
    from app.services.escale_crew import embarkation_alerts

    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(
        id=1,
        leg_code="1CFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=base,
        eta_ref=base,
        etd=base,
        eta=base,
    )
    # embarquement APRÈS l'ETD + sans billet → 2 alertes
    a = CrewAssignment(crew_member_id=5, leg_id=1, embark_at=base + timedelta(days=2))
    alerts = embarkation_alerts([a], leg)
    levels = {al["level"] for al in alerts}
    assert "warning" in levels  # embarquement après ETD
    assert "info" in levels  # billet non chargé


@pytest.mark.asyncio
async def test_crew_assignment_alerts_per_assignment(db):
    """CREW-07 — alertes billet/escale indexées par affectation (fiche marin)."""
    from app.services.escale_crew import crew_assignment_alerts

    leg = await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()
    # Affectation incohérente : embarquement après l'ETD + billet absent.
    a_bad = CrewAssignment(
        crew_member_id=m.id, leg_id=leg.id, embark_at=leg.etd + timedelta(days=3)
    )
    # Affectation cohérente : embarquement avant l'ETD + billet chargé.
    a_ok = CrewAssignment(
        crew_member_id=m.id,
        leg_id=leg.id,
        embark_at=leg.etd - timedelta(days=1),
        ticket_path="crew_tickets/x.pdf",
    )
    db.add_all([a_bad, a_ok])
    await db.flush()

    alerts = await crew_assignment_alerts(db, [a_bad, a_ok])
    assert a_bad.id in alerts and a_ok.id not in alerts  # seul l'incohérent alerte
    msgs = [x["message"] for x in alerts[a_bad.id]]
    assert any("ETD" in msg for msg in msgs)
    assert any("Billet" in msg for msg in msgs)


@pytest.mark.asyncio
async def test_crew_detail_renders_with_alerts(db, staff_user):
    """La fiche marin (panneau alertes CREW-07) se rend sans erreur."""
    from types import SimpleNamespace as _NS

    from app.routers.crew_router import crew_detail

    leg = await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()
    db.add(
        CrewAssignment(crew_member_id=m.id, leg_id=leg.id, embark_at=leg.etd + timedelta(days=2))
    )
    await db.flush()

    class _Req:
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}
        query_params: dict[str, str] = {}
        client = _NS(host="127.0.0.1")
        url = _NS(path=f"/crew/members/{m.id}")
        state = _NS(notif_count=0, newtowt_agent_enabled=True, recent_notifications=[])

    resp = await crew_detail(m.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.context["assignment_alerts"]  # au moins une alerte


@pytest.mark.asyncio
async def test_escale_index_renders_with_crew_panel(db, staff_user):
    """Le rendu de la page escale (avec panneau équipage ESC-06) ne lève pas."""
    from app.routers.escale_router import escale_index

    leg = await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()
    db.add(
        CrewAssignment(
            crew_member_id=m.id,
            leg_id=leg.id,
            vessel_id=1,
            embark_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
    )
    await db.flush()

    class _FullReq:
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}
        query_params: dict[str, str] = {}
        client = SimpleNamespace(host="127.0.0.1")
        url = SimpleNamespace(path="/escale")
        state = SimpleNamespace(notif_count=0, newtowt_agent_enabled=True)

    resp = await escale_index(_FullReq(), leg_id=leg.id, db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.template.name == "staff/escale/index.html"
    assert resp.context["crew_assignments"]
    assert resp.context["embark_alerts"]  # billet non chargé → au moins 1 alerte


# ─────────────────────────────── UX-03 ───────────────────────────────


@pytest.mark.asyncio
async def test_sidebar_port_clock_rendered(db, staff_user):
    """UX-03 : l'horloge sidebar expose le fuseau du port de destination."""
    from app.routers.escale_router import escale_index

    leg = await _setup_leg(db)
    pod = await db.get(Port, 2)  # BRSSO (arrivée)
    pod.timezone = "America/Sao_Paulo"
    await db.flush()

    class _FullReq:
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}
        query_params: dict[str, str] = {}
        client = SimpleNamespace(host="127.0.0.1")
        url = SimpleNamespace(path="/escale")
        state = SimpleNamespace(notif_count=0, newtowt_agent_enabled=True, recent_notifications=[])

    resp = await escale_index(_FullReq(), leg_id=leg.id, db=db, user=staff_user)
    html = resp.body.decode()
    assert 'class="sidebar-clock"' in html
    assert 'data-clock="utc"' in html  # UTC toujours présent
    assert 'data-port-tz="America/Sao_Paulo"' in html  # port de destination
