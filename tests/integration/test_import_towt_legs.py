"""Script de reprise des voyages TOWT — ``scripts.import_towt_legs`` (ADR-014).

Rejoue le CSV réel ``scripts/data/towt_legs_history.csv`` : 36 voyages créés en
lecture seule avec leur TRIP CODE d'origine, ports manquants créés, correction
documentée appliquée, idempotence au rejeu, collision avec un code NEWTOWT bloquante.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.port import Port
from app.models.vessel import Vessel
from scripts import import_towt_legs as script


async def _noop(*_a, **_k):
    return None


async def _setup(db, monkeypatch):
    db.add(Vessel(id=1, code="1", name="Anemos"))
    db.add(Vessel(id=2, code="2", name="Artemis"))
    # Quelques ports déjà connus (avec coordonnées) — les autres viennent du catalogue.
    db.add(
        Port(id=1, locode="FRLEH", name="Le Havre", country="FR", latitude=49.49, longitude=0.11)
    )
    db.add(
        Port(id=2, locode="USNYC", name="New York", country="US", latitude=40.69, longitude=-74.04)
    )
    await db.flush()

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(script, "SessionLocal", _session)
    monkeypatch.setattr(db, "commit", _noop, raising=False)
    monkeypatch.setattr(db, "rollback", _noop, raising=False)


@pytest.mark.asyncio
async def test_import_creates_readonly_archive_legs(db, monkeypatch, capsys):
    await _setup(db, monkeypatch)
    rc = await script.run(script.DEFAULT_FILE, apply=True)
    assert rc == 0
    legs = list((await db.execute(select(Leg))).scalars().all())
    assert len(legs) == 36
    assert all(lg.origin == LEG_ORIGIN_TOWT and lg.is_archive for lg in legs)
    assert all(lg.status == "completed" and lg.phase == "termine" for lg in legs)
    assert all(lg.atd is not None and lg.ata is not None and lg.ata >= lg.atd for lg in legs)
    by_code = {lg.leg_code: lg for lg in legs}
    # TRIP CODE TOWT conservé tel quel, y compris les codes atypiques.
    assert {"1LY1A4", "2VH0A4", "2LQF5-B", "1AYF6"} <= set(by_code)
    # Correction documentée : ATA 2016 → 2026.
    assert by_code["2NZF5"].ata.replace(tzinfo=None) == datetime(2026, 1, 14)
    assert "2016-01-14" in (by_code["2NZF5"].closure_notes or "")
    # Ports manquants créés depuis le catalogue, ports existants intacts.
    ports = {p.locode: p for p in (await db.execute(select(Port))).scalars().all()}
    assert {"COSTM", "GTPBR", "REREU", "CAMAT", "FRCOC", "FRFEC"} <= set(ports)
    assert ports["COSTM"].source == "user" and ports["FRLEH"].name == "Le Havre"
    # Distance théorique posée quand les deux ports ont des coordonnées.
    assert by_code["1LY1A4"].distance_nm is not None
    assert by_code["2LQF5-B"].distance_nm is None  # POL = POD : pas de distance
    out = capsys.readouterr().out
    assert "36" in out and "⚠" in out  # ruptures connues signalées, jamais corrigées

    # Rejeu : idempotent, rien de nouveau.
    rc = await script.run(script.DEFAULT_FILE, apply=True)
    assert rc == 0
    n = (await db.execute(select(func.count()).select_from(Leg))).scalar_one()
    assert n == 36


@pytest.mark.asyncio
async def test_collision_with_newtowt_code_blocks(db, monkeypatch):
    await _setup(db, monkeypatch)
    d = datetime(2026, 1, 13, tzinfo=UTC)
    db.add(
        Leg(
            leg_code="1AYF6",
            vessel_id=1,
            departure_port_id=2,
            arrival_port_id=1,
            etd_ref=d,
            eta_ref=d,
            etd=d,
            eta=d,
        )
    )
    await db.flush()
    rc = await script.run(script.DEFAULT_FILE, apply=True, vessel_filter="ANEMOS")
    assert rc == 1


@pytest.mark.asyncio
async def test_vessel_filter_and_dry_run(db, monkeypatch):
    await _setup(db, monkeypatch)
    rows = script.load_rows(script.DEFAULT_FILE, vessel_filter="ARTEMIS")
    assert len(rows) == 17 and all(r.vessel == "ARTEMIS" for r in rows)
    rc = await script.run(script.DEFAULT_FILE, apply=False, vessel_filter="ARTEMIS")
    assert rc == 0  # dry-run : le rapport est produit, le rollback (neutralisé ici) suit
