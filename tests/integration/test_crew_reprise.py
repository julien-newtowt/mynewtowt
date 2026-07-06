"""Crew P0 — reprise (CREW-01/02/03/04/08) : tests d'intégration."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.crew import CrewAssignment, CrewMember
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    def __init__(self, form: dict | None = None):
        self._form = dict(form or {})
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def form(self):
        return self._form


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
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


# ───────────────────────── CREW-01 / CREW-03 ─────────────────────────


@pytest.mark.asyncio
async def test_crew_edit_updates_erp_fields(db, staff_user):
    """Les marins sont créés par Marad (plus de route de création). L'édition des
    champs ERP (visa, téléphone…) reste possible sur la fiche."""
    from app.routers.crew_router import crew_edit

    m = CrewMember(full_name="Jean Marin", role="capitaine")
    db.add(m)
    await db.flush()

    resp = await crew_edit(
        m.id,
        _Req(form={"role": "capitaine", "visa_br_expires_at": "2029-09-09", "phone": "0102030405"}),
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    member = await db.get(CrewMember, m.id)
    assert member.visa_br_expires_at == date(2029, 9, 9)
    assert member.phone == "0102030405"


def test_crew_create_route_removed():
    """La création de marin est désactivée (les marins proviennent de Marad)."""
    import app.routers.crew_router as cr

    assert not hasattr(cr, "crew_create")
    assert not hasattr(cr, "crew_new_form")
    assert not hasattr(cr, "crew_assign")  # création d'embarquement retirée aussi


# ───────────────────────────── CREW-04 ─────────────────────────────


@pytest.mark.asyncio
async def test_assignment_edit_and_delete(db, staff_user):
    from app.routers.crew_router import crew_assignment_delete, crew_assignment_edit

    await _setup_leg(db)
    db.add(CrewMember(id=1, full_name="Jean Marin", role="capitaine"))
    await db.flush()
    a = CrewAssignment(crew_member_id=1, leg_id=1, embark_at=datetime(2026, 4, 1, tzinfo=UTC))
    db.add(a)
    await db.flush()

    resp = await crew_assignment_edit(
        a.id,
        _Req(),
        leg_id=1,
        role_on_board="capitaine",
        embark_at="2026-04-02T08:00:00",
        disembark_at="2026-04-18T18:00:00",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    await db.refresh(a)
    assert a.role_on_board == "capitaine"
    assert a.embark_at.day == 2

    aid = a.id
    resp = await crew_assignment_delete(aid, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(CrewAssignment, aid)) is None


# ───────────────────────────── CREW-08 ─────────────────────────────


@pytest.mark.asyncio
async def test_find_overlap_detects_and_allows_handover(db):
    """Overlap détecté ; relève le même jour (bornes qui se touchent) permise."""
    from app.routers.crew_router import _find_overlap

    await _setup_leg(db)
    db.add(CrewMember(id=1, full_name="Jean Marin", role="capitaine"))
    await db.flush()
    a1 = CrewAssignment(
        crew_member_id=1,
        leg_id=1,
        embark_at=datetime(2026, 4, 1, tzinfo=UTC),
        disembark_at=datetime(2026, 4, 20, tzinfo=UTC),
    )
    db.add(a1)
    await db.flush()

    # Période chevauchante → détectée.
    ov = await _find_overlap(
        db,
        member_id=1,
        embark=datetime(2026, 4, 10),
        disembark=datetime(2026, 4, 25),
    )
    assert ov is not None and ov.id == a1.id

    # Relève le même jour (embarque pile au débarquement) → autorisée.
    none = await _find_overlap(
        db,
        member_id=1,
        embark=datetime(2026, 4, 20),
        disembark=datetime(2026, 5, 1),
    )
    assert none is None

    # On s'exclut soi-même.
    assert (
        await _find_overlap(
            db,
            member_id=1,
            embark=datetime(2026, 4, 1),
            disembark=datetime(2026, 4, 20),
            exclude_id=a1.id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_assignment_edit_rejects_overlap(db, staff_user):
    from fastapi import HTTPException

    from app.routers.crew_router import crew_assignment_edit

    await _setup_leg(db)
    db.add(CrewMember(id=1, full_name="Jean Marin", role="capitaine"))
    await db.flush()
    a1 = CrewAssignment(
        crew_member_id=1,
        leg_id=1,
        embark_at=datetime(2026, 4, 1, tzinfo=UTC),
        disembark_at=datetime(2026, 4, 10, tzinfo=UTC),
    )
    a2 = CrewAssignment(
        crew_member_id=1,
        leg_id=1,
        embark_at=datetime(2026, 4, 15, tzinfo=UTC),
        disembark_at=datetime(2026, 4, 25, tzinfo=UTC),
    )
    db.add_all([a1, a2])
    await db.flush()

    # Édite a2 pour chevaucher a1 → rejeté (400).
    with pytest.raises(HTTPException) as exc:
        await crew_assignment_edit(
            a2.id,
            _Req(),
            leg_id=1,
            role_on_board=None,
            embark_at="2026-04-05T00:00:00",
            disembark_at="2026-04-20T00:00:00",
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400


# ───────────────────────────── CREW-02 ─────────────────────────────


@pytest.mark.asyncio
async def test_border_police_pdf_renders(db, staff_user):
    from app.routers.crew_router import crew_border_police_pdf

    await _setup_leg(db)
    db.add(
        CrewMember(
            id=1,
            full_name="Jean Marin",
            role="capitaine",
            nationality="FR",
            passport_number="12AB34567",
            passport_expires_at=date(2030, 1, 1),
        )
    )
    db.add(CrewMember(id=2, full_name="John Sailor", role="marin", nationality="GB"))
    await db.flush()
    db.add(CrewAssignment(crew_member_id=1, leg_id=1, embark_at=datetime(2026, 4, 1, tzinfo=UTC)))
    db.add(CrewAssignment(crew_member_id=2, leg_id=1, embark_at=datetime(2026, 4, 1, tzinfo=UTC)))
    await db.flush()

    resp = await crew_border_police_pdf(1, db=db, user=staff_user)
    assert resp.media_type == "application/pdf"
    assert len(resp.body) > 500
