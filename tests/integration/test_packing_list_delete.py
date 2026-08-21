"""CARGO-14 — suppression d'une packing list entière côté staff (perm S).

Vérifie la suppression (avec cascade ORM des batches), le 404 sur PL absente et
l'enregistrement de la route. La PL verrouillée est protégée (cf. ``can_modify``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from tests.integration.conftest import FakeRequest


async def _setup_booking(db):
    """Chaîne minimale navire → ports → leg → booking.

    Requise depuis l'ajout de ``ck_packing_lists_order_xor_booking`` : une PL
    doit appartenir à une commande **ou** à un booking (XOR), et la FK est
    réellement appliquée. La fixture d'origine créait un ``PackingList`` nu,
    ce qui échouait en IntegrityError.
    """
    from app.models.booking import Booking
    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel

    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    db.add(
        Leg(
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
    )
    await db.flush()
    db.add(Booking(id=1, reference="BK-PL-DEL", leg_id=1, status="confirmed"))
    await db.flush()


@pytest.mark.asyncio
async def test_delete_packing_list_removes_it(db, staff_user):
    from app.models.packing_list import PackingList, PackingListBatch
    from app.routers.cargo_packing_router import delete_packing_list

    await _setup_booking(db)
    db.add(PackingList(id=1, booking_id=1))
    await db.flush()
    db.add(PackingListBatch(packing_list_id=1, pallet_format="EPAL", pallet_count=2))
    await db.flush()

    resp = await delete_packing_list(1, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert await db.get(PackingList, 1) is None


@pytest.mark.asyncio
async def test_delete_missing_packing_list_404(db, staff_user):
    from app.routers.cargo_packing_router import delete_packing_list

    with pytest.raises(HTTPException) as exc:
        await delete_packing_list(999, FakeRequest(), db=db, user=staff_user)
    assert exc.value.status_code == 404


def test_delete_route_registered():
    from app.routers import cargo_packing_router

    paths = {r.path for r in cargo_packing_router.router.routes}
    assert any(p.endswith("/{pl_id}/delete") for p in paths)
