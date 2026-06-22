"""Reprise P0 additive : COM-04 (PJ commande), MRV-07 (auto GPS→DMS),
CREW-04 (embarquement hors leg), CREW-05 (billet)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.claim import VesselPosition
from app.models.commercial import Client, Order
from app.models.crew import CrewAssignment, CrewMember
from app.models.leg import Leg
from app.models.mrv import MRVEvent
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


class _Upload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


class _FormReq:
    def __init__(self, form: dict):
        self._form = form
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def form(self):
        return self._form


@pytest.fixture
def _upload_root(tmp_path, monkeypatch):
    import app.services.safe_files as sf

    monkeypatch.setattr(sf, "_upload_root", lambda: tmp_path)
    return tmp_path


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(id=1, leg_code="1CFRBR6", vessel_id=1, departure_port_id=1, arrival_port_id=2,
              etd_ref=base, eta_ref=base + timedelta(days=20), etd=base, eta=base + timedelta(days=20))
    db.add(leg)
    await db.flush()
    return leg


# ─────────────────────────────── COM-04 ───────────────────────────────


@pytest.mark.asyncio
async def test_order_attachment_upload_download_delete(db, staff_user, _upload_root):
    from app.routers.commercial_router import (
        order_delete_attachment,
        order_download_attachment,
        order_upload_attachment,
    )

    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    await db.flush()
    order = Order(reference="ORD-1", client_id=c.id, status="draft")
    db.add(order)
    await db.flush()

    resp = await order_upload_attachment(
        order.id, _Req(), file=_Upload("contrat.pdf", b"%PDF-1.4 contrat"),
        db=db, user=staff_user,
    )
    assert resp.status_code in (200, 303)
    await db.refresh(order)
    assert order.attachment_path and order.attachment_filename == "contrat.pdf"
    assert (_upload_root / order.attachment_path).is_file()

    dl = await order_download_attachment(order.id, db=db, user=staff_user)
    assert dl.status_code == 200

    await order_delete_attachment(order.id, _Req(), db=db, user=staff_user)
    await db.refresh(order)
    assert order.attachment_path is None


# ─────────────────────────────── MRV-07 ───────────────────────────────


def test_decimal_to_dms():
    from app.services.mrv_compute import decimal_to_dms

    deg, minutes, hemi = decimal_to_dms(49.5, is_lat=True)
    assert (deg, hemi) == (49, "N")
    assert minutes == Decimal("30.000")
    deg, minutes, hemi = decimal_to_dms(-0.25, is_lat=False)
    assert (deg, hemi) == (0, "W")
    assert minutes == Decimal("15.000")


@pytest.mark.asyncio
async def test_mrv_add_event_autofills_dms_from_last_position(db, staff_user):
    from app.routers.mrv_router import add_event

    await _setup_leg(db)
    db.add(VesselPosition(vessel_id=1, recorded_at=datetime(2026, 4, 2, 6, tzinfo=UTC),
                          latitude=49.5, longitude=-0.25))
    await db.flush()

    await add_event(
        1, _FormReq({"event_kind": "noon_consumption", "recorded_at": "2026-04-02T12:00:00",
                     "fuel_mass_t": "5.0"}),
        db=db, user=staff_user,
    )
    ev = (await db.execute(MRVEvent.__table__.select())).fetchone()
    assert ev.lat_deg == 49 and ev.lat_ns == "N"
    assert ev.lon_deg == 0 and ev.lon_ew == "W"


@pytest.mark.asyncio
async def test_mrv_autofill_does_not_override_manual_position(db, staff_user):
    from app.routers.mrv_router import add_event

    await _setup_leg(db)
    db.add(VesselPosition(vessel_id=1, recorded_at=datetime(2026, 4, 2, 6, tzinfo=UTC),
                          latitude=49.5, longitude=-0.25))
    await db.flush()
    await add_event(
        1, _FormReq({"event_kind": "noon_consumption", "recorded_at": "2026-04-02T12:00:00",
                     "fuel_mass_t": "5.0", "lat_deg": "10", "lat_ns": "S"}),
        db=db, user=staff_user,
    )
    ev = (await db.execute(MRVEvent.__table__.select())).fetchone()
    assert ev.lat_deg == 10 and ev.lat_ns == "S"  # saisie manuelle préservée


# ─────────────────────────── CREW-04 / CREW-05 ───────────────────────────


@pytest.mark.asyncio
async def test_crew_embark_off_leg(db, staff_user):
    """CREW-04 (A4) — embarquement rattaché au navire sans leg précis."""
    from app.routers.crew_router import crew_assign

    await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()

    resp = await crew_assign(
        m.id, _Req(), leg_id=None, vessel_id=1, role_on_board="matelot",
        embark_at="2026-04-01T08:00:00", disembark_at="2026-04-10T08:00:00",
        override_compliance=None, db=db, user=staff_user,
    )
    assert resp.status_code == 303
    a = (await db.execute(CrewAssignment.__table__.select())).fetchone()
    assert a.leg_id is None and a.vessel_id == 1


@pytest.mark.asyncio
async def test_crew_embark_requires_leg_or_vessel(db, staff_user):
    from fastapi import HTTPException

    from app.routers.crew_router import crew_assign

    await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await crew_assign(
            m.id, _Req(), leg_id=None, vessel_id=None, role_on_board=None,
            embark_at=None, disembark_at=None, override_compliance=None,
            db=db, user=staff_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_crew_ticket_upload_download_delete(db, staff_user, _upload_root):
    from app.routers.crew_router import (
        crew_assignment_ticket_delete,
        crew_assignment_ticket_download,
        crew_assignment_ticket_upload,
    )

    await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()
    a = CrewAssignment(crew_member_id=m.id, leg_id=1)
    db.add(a)
    await db.flush()

    await crew_assignment_ticket_upload(
        a.id, _Req(), file=_Upload("billet.pdf", b"%PDF-1.4 billet"), db=db, user=staff_user,
    )
    await db.refresh(a)
    assert a.ticket_path and a.ticket_filename == "billet.pdf"

    dl = await crew_assignment_ticket_download(a.id, db=db, user=staff_user)
    assert dl.status_code == 200

    await crew_assignment_ticket_delete(a.id, _Req(), db=db, user=staff_user)
    await db.refresh(a)
    assert a.ticket_path is None
