"""STO-10 — API JSON d'occupation des zones d'arrimage d'un leg.

Endpoint lecture seule `/stowage/legs/{id}/occupation.json` : structure (par cale
+ par zone), sérialisation du poids en flottant, 404 sur leg absent.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from tests.integration.conftest import _setup_leg


@pytest.mark.asyncio
async def test_occupation_json_structure(db, staff_user):
    from app.routers.stowage_router import stowage_occupation_json

    await _setup_leg(db)  # leg id=1, leg_code 1CFRBR6
    payload = await stowage_occupation_json(1, db=db, user=staff_user)

    assert payload["leg_id"] == 1
    assert payload["leg_code"] == "1CFRBR6"
    # Par cale : les deux cales AR/AV, poids en flottant (JSON-safe).
    assert set(payload["by_hold"]) == {"AR", "AV"}
    assert payload["by_hold"]["AR"]["pallet_count"] == 0
    assert isinstance(payload["by_hold"]["AR"]["weight_kg"], float)
    # Par zone : liste (vide sans plan).
    assert isinstance(payload["by_zone"], list)


@pytest.mark.asyncio
async def test_occupation_json_404(db, staff_user):
    from app.routers.stowage_router import stowage_occupation_json

    with pytest.raises(HTTPException) as exc:
        await stowage_occupation_json(999, db=db, user=staff_user)
    assert exc.value.status_code == 404


def test_occupation_route_registered():
    from app.routers import stowage_router

    paths = {r.path for r in stowage_router.router.routes}
    assert "/stowage/legs/{leg_id}/occupation.json" in paths
