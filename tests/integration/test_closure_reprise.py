"""Onboard P1 — reprise (ONB-05 clôture : reopen + checklist + récap PDF)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.sof_event import CargoDocument, SofEvent
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _setup_leg(db, **closure):
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
        ata=base + timedelta(days=20),
        atd=base,
        status="in_progress",
        **closure,
    )
    db.add(leg)
    await db.flush()
    return leg


@pytest.mark.asyncio
async def test_closure_checklist(db):
    from app.services.closure import closure_checklist

    leg = await _setup_leg(db)
    db.add(SofEvent(leg_id=1, event_type="SOSP", occurred_at=datetime(2026, 4, 1, tzinfo=UTC)))
    db.add(CargoDocument(leg_id=1, kind="NOR", issued_at=datetime(2026, 4, 1, tzinfo=UTC)))
    await db.flush()

    items = {c["label"]: c["present"] for c in await closure_checklist(db, leg)}
    assert items["Départ consigné (SOSP)"] is True
    assert items["Arrivée consignée (EOSP)"] is False
    assert items["Notice of Readiness"] is True
    assert items["ATA posée"] is True


@pytest.mark.asyncio
async def test_closure_reopen_clears_validation(db, staff_user):
    from app.routers.captain_router import closure_reopen

    now = datetime.now(UTC)
    leg = await _setup_leg(
        db,
        closure_submitted_at=now,
        closure_submitted_by="cmdt",
        closure_reviewed_at=now,
        closure_approved_at=now,
    )
    leg.status = "completed"
    await db.flush()

    resp = await closure_reopen(1, _Req(), notes="erreur SOF", db=db, user=staff_user)
    assert resp.status_code == 303
    await db.refresh(leg)
    assert leg.closure_submitted_at is None
    assert leg.closure_reviewed_at is None
    assert leg.closure_approved_at is None
    assert leg.status == "in_progress"


@pytest.mark.asyncio
async def test_closure_reopen_rejected_when_not_started(db, staff_user):
    from fastapi import HTTPException

    from app.routers.captain_router import closure_reopen

    await _setup_leg(db)
    with pytest.raises(HTTPException) as exc:
        await closure_reopen(1, _Req(), notes=None, db=db, user=staff_user)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_closure_recap_pdf_renders(db, staff_user):
    pytest.importorskip("weasyprint")
    from app.routers.captain_router import closure_recap_pdf

    await _setup_leg(db, closure_submitted_at=datetime.now(UTC), closure_submitted_by="cmdt")
    db.add(SofEvent(leg_id=1, event_type="EOSP", occurred_at=datetime(2026, 4, 20, tzinfo=UTC)))
    await db.flush()
    resp = await closure_recap_pdf(1, db=db, user=staff_user)
    assert resp.media_type == "application/pdf"
    assert len(resp.body) > 500
