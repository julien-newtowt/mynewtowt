"""Import des positions GPS d'archive TOWT + protection contre la purge (ADR-014)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models.claim import VesselPosition
from app.models.vessel import Vessel
from app.services import admin_data
from scripts import import_towt_positions as script

CSV = """vessel,recorded_at_utc,latitude,longitude,sog_kn,cog_deg,interface,source_file
anemos,2024-10-21T09:05:04Z,49.27073,-16.83568,9,95,Starlink_1921681001,20241021100502-anemos-satcoms.csv
anemos,2024-10-21T09:10:03Z,49.26956,-16.81571,9,95,Starlink_1921681001,20241021100502-anemos-satcoms.csv
anemos,2024-10-21T09:15:10Z,49.26832,-16.79425,,,Starlink_1921681001,20241021100502-anemos-satcoms.csv
anemos,2024-10-21T09:15:10Z,49.26832,-16.79425,10,95,x,dup.csv
anemos,2024-10-21T09:20:09Z,49.26702,-16.77273,99,95,x,aberrant-sog.csv
anemos,bad-date,49.0,-16.0,1,1,x,bad.csv
"""


async def _noop(*_a, **_k):
    return None


async def _setup(db, monkeypatch, tmp_path):
    db.add(Vessel(id=1, code="1", name="Anemos"))
    await db.flush()
    db.add(
        VesselPosition(
            vessel_id=1,
            recorded_at=datetime(2024, 10, 21, 9, 5, 4, tzinfo=UTC),
            latitude=49.27073,
            longitude=-16.83568,
            source="satcom",
        )
    )
    await db.flush()
    path = tmp_path / "towt_gps_anemos_2024.csv"
    path.write_text(CSV, encoding="utf-8")

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(script, "SessionLocal", _session)
    monkeypatch.setattr(db, "commit", _noop, raising=False)
    monkeypatch.setattr(db, "rollback", _noop, raising=False)
    return path


@pytest.mark.asyncio
async def test_import_is_idempotent_and_tagged(db, monkeypatch, tmp_path):
    path = await _setup(db, monkeypatch, tmp_path)
    rc = await script.run([path], apply=True)
    assert rc == 0
    rows = list(
        (await db.execute(select(VesselPosition).order_by(VesselPosition.recorded_at))).scalars()
    )
    # 1 live préexistante + 3 archive (le doublon et la ligne invalide écartés).
    assert len(rows) == 4
    archive = [r for r in rows if r.source == admin_data.TOWT_ARCHIVE_SOURCE]
    assert len(archive) == 3
    assert all(r.import_batch == "towt_gps_anemos_2024.csv" for r in archive)
    assert rows[0].source == "satcom"  # le point live n'est pas réécrit
    aberrant = next(r for r in archive if r.recorded_at.minute == 20)
    assert aberrant.sog_kn is None  # SOG hors plage ignorée, point conservé

    rc = await script.run([path], apply=True)
    assert rc == 0
    n = (await db.execute(select(func.count()).select_from(VesselPosition))).scalar_one()
    assert n == 4


@pytest.mark.asyncio
async def test_purge_never_touches_archive_rows(db, monkeypatch, tmp_path):
    path = await _setup(db, monkeypatch, tmp_path)
    await script.run([path], apply=True)
    # Purge par rétention : tout est « ancien » — seul le point live disparaît.
    removed = await admin_data.purge_table_before(
        db, "vessel_positions", datetime(2030, 1, 1, tzinfo=UTC)
    )
    assert removed == 1
    # Vidage intégral : idem, l'archive reste.
    removed = await admin_data.purge_table(db, "vessel_positions")
    assert removed == 0
    left = list((await db.execute(select(VesselPosition))).scalars())
    assert len(left) == 3 and all(r.source == admin_data.TOWT_ARCHIVE_SOURCE for r in left)


@pytest.mark.asyncio
async def test_unknown_vessel_blocks(db, monkeypatch, tmp_path):
    path = await _setup(db, monkeypatch, tmp_path)
    other = tmp_path / "towt_gps_artemis_2024.csv"
    other.write_text(CSV.replace("anemos,", "artemis,"), encoding="utf-8")
    rc = await script.run([path, other], apply=True)
    assert rc == 1
