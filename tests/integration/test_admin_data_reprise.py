"""Admin P1 — reprise (ADM-04 exports CSV/ZIP + purges whitelistées)."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.activity_log import ActivityLog
from app.models.claim import VesselPosition
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


# ───────────────────────────── Export ─────────────────────────────


@pytest.mark.asyncio
async def test_export_table_csv_whitelisted(db):
    from app.services.admin_data import export_table_csv

    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    await db.flush()
    csv_text = await export_table_csv(db, "vessels")
    assert "code" in csv_text.splitlines()[0]  # en-tête
    assert "ANE" in csv_text


@pytest.mark.asyncio
async def test_export_table_rejects_non_whitelisted(db):
    from app.services.admin_data import export_table_csv

    # users contient des secrets → non exportable
    with pytest.raises(ValueError):
        await export_table_csv(db, "users")
    with pytest.raises(ValueError):
        await export_table_csv(db, "definitely_not_a_table")


@pytest.mark.asyncio
async def test_export_global_zip(db, staff_user):
    from app.routers.admin_router import admin_export_global

    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    await db.flush()
    resp = await admin_export_global(_Req(), db=db, user=staff_user)
    assert resp.media_type == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.body))
    names = zf.namelist()
    assert "vessels.csv" in names
    assert "MANIFEST.txt" in names
    assert "users.csv" not in names  # secrets exclus


# ───────────────────────────── Purge ─────────────────────────────


@pytest.mark.asyncio
async def test_purge_table_whitelisted(db):
    from app.services.admin_data import purge_table

    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    await db.flush()
    db.add(
        VesselPosition(
            vessel_id=1,
            recorded_at=datetime(2026, 4, 1, tzinfo=UTC),
            latitude=1.0,
            longitude=2.0,
            source="manual",
        )
    )
    db.add(
        VesselPosition(
            vessel_id=1,
            recorded_at=datetime(2026, 4, 2, tzinfo=UTC),
            latitude=1.0,
            longitude=2.0,
            source="manual",
        )
    )
    await db.flush()
    deleted = await purge_table(db, "vessel_positions")
    assert deleted == 2
    assert (await db.execute(VesselPosition.__table__.select())).fetchone() is None


@pytest.mark.asyncio
async def test_purge_rejects_non_whitelisted(db):
    from app.services.admin_data import purge_table

    # legs n'est PAS purgeable (donnée métier structurante)
    with pytest.raises(ValueError):
        await purge_table(db, "legs")
    with pytest.raises(ValueError):
        await purge_table(db, "users")


@pytest.mark.asyncio
async def test_purge_route_requires_exact_confirmation(db, staff_user):
    from fastapi import HTTPException

    from app.routers.admin_router import admin_purge_table

    db.add(ActivityLog(action="x", module="admin", entity_type="t", user_name="u"))
    await db.flush()
    # mauvaise confirmation → 400, rien supprimé
    with pytest.raises(HTTPException) as exc:
        await admin_purge_table(
            _Req(), table_name="activity_logs", confirm="wrong", db=db, user=staff_user
        )
    assert exc.value.status_code == 400
    assert (await db.execute(ActivityLog.__table__.select())).fetchone() is not None

    # bonne confirmation → purge
    resp = await admin_purge_table(
        _Req(), table_name="activity_logs", confirm="activity_logs", db=db, user=staff_user
    )
    assert resp.status_code == 303


# ──────────────────────── Purge ciblée par rétention ────────────────────────


def test_purge_date_columns_cover_all_purge_tables():
    """Garde de complétude : chaque table purgeable déclare sa colonne de date."""
    from app.services.admin_data import ALLOWED_PURGE_TABLES, PURGE_DATE_COLUMNS

    assert set(ALLOWED_PURGE_TABLES) == set(PURGE_DATE_COLUMNS)


@pytest.mark.asyncio
async def test_purge_table_before_keeps_recent_rows(db):
    from app.services.admin_data import purge_table_before

    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    await db.flush()
    old = VesselPosition(
        vessel_id=1,
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        latitude=1.0,
        longitude=2.0,
        source="manual",
    )
    recent = VesselPosition(
        vessel_id=1,
        recorded_at=datetime(2026, 6, 1, tzinfo=UTC),
        latitude=3.0,
        longitude=4.0,
        source="manual",
    )
    db.add_all([old, recent])
    await db.flush()

    # Supprime ce qui est antérieur au 2026-03-01 : seule la ligne « old » part.
    cutoff = datetime(2026, 3, 1, tzinfo=UTC)
    deleted = await purge_table_before(db, "vessel_positions", cutoff)
    assert deleted == 1
    remaining = (await db.execute(VesselPosition.__table__.select())).fetchall()
    assert len(remaining) == 1
    assert remaining[0].latitude == 3.0  # la récente subsiste


@pytest.mark.asyncio
async def test_purge_table_before_rejects_non_whitelisted(db):
    from app.services.admin_data import purge_table_before

    with pytest.raises(ValueError):
        await purge_table_before(db, "legs", datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def test_purge_route_retention_only_old(db, staff_user):
    from app.routers.admin_router import admin_purge_table

    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    await db.flush()
    db.add_all(
        [
            VesselPosition(
                vessel_id=1,
                recorded_at=datetime(2020, 1, 1, tzinfo=UTC),
                latitude=1.0,
                longitude=2.0,
                source="manual",
            ),
            VesselPosition(
                vessel_id=1,
                recorded_at=datetime.now(UTC),
                latitude=3.0,
                longitude=4.0,
                source="manual",
            ),
        ]
    )
    await db.flush()

    resp = await admin_purge_table(
        _Req(),
        table_name="vessel_positions",
        confirm="vessel_positions",
        older_than_days=30,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    remaining = (await db.execute(VesselPosition.__table__.select())).fetchall()
    assert len(remaining) == 1  # seule la ligne ancienne a été purgée
