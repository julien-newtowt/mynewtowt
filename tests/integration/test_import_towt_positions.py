"""Import des positions GPS d'archive TOWT + protection contre la purge (ADR-014)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models.claim import VesselPosition
from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.port import Port
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
    db.add(Port(id=1, locode="CAQUE", name="Québec", country="CA"))
    db.add(Port(id=2, locode="FRLEH", name="Le Havre", country="FR"))
    await db.flush()
    # Un leg d'archive borne la reprise : ATA 2024-10-24 → borne au 2024-10-25.
    db.add(
        Leg(
            id=1,
            leg_code="1QLD4",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=datetime(2024, 10, 10, tzinfo=UTC),
            eta_ref=datetime(2024, 10, 24, tzinfo=UTC),
            etd=datetime(2024, 10, 10, tzinfo=UTC),
            eta=datetime(2024, 10, 24, tzinfo=UTC),
            atd=datetime(2024, 10, 10, tzinfo=UTC),
            ata=datetime(2024, 10, 24, tzinfo=UTC),
            status="completed",
            origin=LEG_ORIGIN_TOWT,
        )
    )
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


@pytest.mark.asyncio
async def test_refuses_file_of_vessel_without_archive_leg(db, monkeypatch, tmp_path):
    """Atlantis / Atlas (navires NEWTOWT) : aucun leg d'archive → refus net.

    Sans cette borne, des positions vivantes seraient étiquetées
    ``towt_archive`` et deviendraient impurgeables (ADR-014, décision 4).
    """
    await _setup(db, monkeypatch, tmp_path)
    db.add(Vessel(id=3, code="3", name="Atlantis"))
    await db.flush()
    other = tmp_path / "towt_gps_atlantis_2026.csv"
    other.write_text(
        CSV.replace("anemos,", "atlantis,").replace("2024-10-21", "2026-08-07"),
        encoding="utf-8",
    )
    rc = await script.run([other], apply=True)
    assert rc == 1
    left = list((await db.execute(select(VesselPosition))).scalars())
    assert all(r.source != admin_data.TOWT_ARCHIVE_SOURCE for r in left)


@pytest.mark.asyncio
async def test_points_after_archive_cutoff_are_ignored(db, monkeypatch, tmp_path):
    """Le fichier 2026 d'ANEMOS couvre janvier → septembre : seuls les points
    antérieurs à la borne (dernier leg d'archive + 1 j) sont repris."""
    await _setup(db, monkeypatch, tmp_path)
    path = tmp_path / "towt_gps_anemos_2024b.csv"
    path.write_text(
        "vessel,recorded_at_utc,latitude,longitude,sog_kn,cog_deg,interface,source_file\n"
        "anemos,2024-10-23T10:00:00Z,49.1,-1.0,8,90,x,a.csv\n"  # avant la borne
        "anemos,2024-10-24T23:00:00Z,49.2,-1.1,8,90,x,a.csv\n"  # jour d'arrivée : gardé
        "anemos,2024-10-25T10:00:00Z,49.3,-1.2,8,90,x,a.csv\n"  # après : ignoré
        "anemos,2026-06-01T10:00:00Z,49.4,-1.3,8,90,x,a.csv\n",  # période NEWTOWT
        encoding="utf-8",
    )
    rep = await script.import_file(db, path)
    assert rep.errors == []
    assert rep.cutoff == "2024-10-25"
    assert rep.inserted == 2 and rep.skipped_after_cutoff == 2


@pytest.mark.asyncio
async def test_until_overrides_the_computed_cutoff(db, monkeypatch, tmp_path):
    path = await _setup(db, monkeypatch, tmp_path)
    # Borne explicite à 09:10 (exclusive) : seul le point de 09:05 reste, et il
    # est déjà en base → rien d'inséré, les trois autres sont hors borne.
    rep = await script.import_file(db, path, until=datetime(2024, 10, 21, 9, 10, tzinfo=UTC))
    assert rep.cutoff == "2024-10-21"
    assert rep.skipped_after_cutoff == 3
    assert rep.inserted == 0 and rep.skipped_existing == 1
