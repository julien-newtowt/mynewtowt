"""Tracking P1 — reprise (TRK-01 /latest, TRK-05 import_batch/created_at)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.claim import VesselPosition
from app.models.vessel import Vessel


async def _vessels(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Vessel(id=2, code="ART", name="Artemis"))
    await db.flush()


# ─────────────────────────────── TRK-05 ───────────────────────────────


def test_vessel_position_has_import_traceability():
    for f in ("import_batch", "created_at"):
        assert hasattr(VesselPosition, f), f


# ─────────────────────────────── TRK-01 ───────────────────────────────


@pytest.mark.asyncio
async def test_latest_positions_one_per_vessel(db):
    from app.routers.tracking_router import latest_positions

    await _vessels(db)
    base = datetime(2026, 4, 1, tzinfo=UTC)
    # Navire 1 : 2 points → on garde le plus récent.
    db.add(
        VesselPosition(
            vessel_id=1, recorded_at=base, latitude=49.0, longitude=0.0, sog_kn=5.0, source="manual"
        )
    )
    db.add(
        VesselPosition(
            vessel_id=1,
            recorded_at=base + timedelta(hours=6),
            latitude=49.5,
            longitude=-1.0,
            sog_kn=8.0,
            source="satcom",
        )
    )
    # Navire 2 : 1 point.
    db.add(
        VesselPosition(
            vessel_id=2,
            recorded_at=base,
            latitude=12.0,
            longitude=-61.0,
            sog_kn=0.0,
            source="manual",
        )
    )
    await db.flush()

    rows = await latest_positions(db)
    assert len(rows) == 2  # une ligne par navire
    by_code = {r["vessel_code"]: r for r in rows}
    assert by_code["ANE"]["lat"] == 49.5  # le plus récent
    assert by_code["ANE"]["sog"] == 8.0
    assert by_code["ANE"]["source"] == "satcom"
    assert by_code["ART"]["lat"] == 12.0
    # contrat de réponse
    assert set(rows[0]) >= {
        "vessel_id",
        "vessel_code",
        "vessel_name",
        "lat",
        "lon",
        "sog",
        "cog",
        "recorded_at",
        "source",
    }


@pytest.mark.asyncio
async def test_latest_positions_empty(db):
    from app.routers.tracking_router import latest_positions

    await _vessels(db)
    assert await latest_positions(db) == []


@pytest.mark.asyncio
async def test_latest_endpoint_returns_json(db, staff_user):
    from app.routers.tracking_router import get_latest_positions

    await _vessels(db)
    db.add(
        VesselPosition(
            vessel_id=1,
            recorded_at=datetime(2026, 4, 1, tzinfo=UTC),
            latitude=49.0,
            longitude=0.0,
            source="manual",
        )
    )
    await db.flush()
    resp = await get_latest_positions(db=db, user=staff_user)
    assert resp.status_code == 200
    import json

    data = json.loads(resp.body)
    assert data[0]["vessel_code"] == "ANE"
