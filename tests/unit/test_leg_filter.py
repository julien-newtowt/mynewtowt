"""Tests du module de filtrage leg — options « Leg lié » enrichies & triées."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (enregistre les modèles)
from app.database import Base
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.leg_filter import format_leg_option, leg_select_options


def _mk_leg(vessel_id, p1, p2, code, month, atd=None, ata=None):
    d = datetime(2026, month, 1, 12, tzinfo=UTC)
    a = datetime(2026, month, 20, 16, tzinfo=UTC)
    return Leg(
        leg_code=code,
        vessel_id=vessel_id,
        departure_port_id=p1,
        arrival_port_id=p2,
        etd=d,
        eta=a,
        etd_ref=d,
        eta_ref=a,
        atd=atd,
        ata=ata,
    )


async def _run():
    eng = create_async_engine("sqlite+aiosqlite://")
    try:
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as s:
            v = Vessel(code="ANE", name="Anemos")
            s.add(v)
            await s.flush()
            p1 = Port(name="Fécamp", locode="FRFEC", country="FR")
            p2 = Port(name="Santos", locode="BRSSZ", country="BR")
            s.add_all([p1, p2])
            await s.flush()
            s.add_all(
                [
                    _mk_leg(v.id, p1.id, p2.id, "3CFRBR6", 8),
                    _mk_leg(
                        v.id, p1.id, p2.id, "1AFRBR6", 6, atd=datetime(2026, 6, 2, 9, tzinfo=UTC)
                    ),
                    _mk_leg(v.id, p1.id, p2.id, "2BFRBR6", 7),
                ]
            )
            await s.flush()
            opts = await leg_select_options(s)
            return opts
    finally:
        await eng.dispose()


def test_leg_options_sorted_chronologically_and_labelled():
    opts = asyncio.run(_run())
    # Tri chronologique par ETD (juin → juillet → août).
    assert [o["leg_code"] for o in opts] == ["1AFRBR6", "2BFRBR6", "3CFRBR6"]
    # Libellé : Année · POL→POD · ETD/ATD · ETA/ATA.
    label = opts[0]["label"]
    assert label.startswith("1AFRBR6 · 2026 · FRFEC→BRSSZ ·")
    assert "ETD 01/06/ATD 02/06" in label
    assert "ETA 20/06/ATA —" in label


def test_format_leg_option_handles_missing_ports_and_dates():
    leg = Leg(
        leg_code="XX",
        vessel_id=1,
        departure_port_id=None,
        arrival_port_id=None,
        etd=None,
        eta=None,
    )
    label = format_leg_option(leg, {})
    assert "?→?" in label
    assert "ETD —/ATD —" in label
    assert "ETA —/ATA —" in label
