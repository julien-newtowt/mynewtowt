"""CARGO-14 — suppression d'une packing list entière côté staff (perm S).

Vérifie la suppression (avec cascade ORM des batches), le 404 sur PL absente et
l'enregistrement de la route. La PL verrouillée est protégée (cf. ``can_modify``).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from tests.integration.conftest import FakeRequest


@pytest.mark.asyncio
async def test_delete_packing_list_removes_it(db, staff_user):
    from app.models.packing_list import PackingList, PackingListBatch
    from app.routers.cargo_packing_router import delete_packing_list

    db.add(PackingList(id=1))
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
