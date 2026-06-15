"""Tests Carbon Report — calcul auto des émissions CO₂ d'un leg (CFOTE_09)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (enregistre les modèles)
from app.database import Base
from app.models.leg import Leg
from app.models.noon_report import NoonReport
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.carbon import compute_carbon_for_leg


async def _scenario():
    eng = create_async_engine("sqlite+aiosqlite://")
    try:
        return await _run(eng)
    finally:
        await eng.dispose()


async def _run(eng):
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(UTC)
    async with Session() as s:
        v = Vessel(code="ANE", name="Anemos")
        s.add(v)
        await s.flush()
        p1 = Port(name="A", locode="FRFEC", country="FR")
        p2 = Port(name="B", locode="BRSSZ", country="BR")
        s.add_all([p1, p2])
        await s.flush()
        leg = Leg(
            leg_code="1AFRBR6",
            vessel_id=v.id,
            departure_port_id=p1.id,
            arrival_port_id=p2.id,
            etd=now,
            eta=now,
            etd_ref=now,
            eta_ref=now,
        )
        s.add(leg)
        await s.flush()
        # Deux noon reports : conso DO totale 1.3 + 0.7 = 2.0 t.
        s.add(
            NoonReport(
                leg_id=leg.id, recorded_at=now, latitude=0, longitude=0, total_consumption_t=1.3
            )
        )
        s.add(
            NoonReport(
                leg_id=leg.id, recorded_at=now, latitude=0, longitude=0, total_consumption_t=0.7
            )
        )
        await s.flush()
        return await compute_carbon_for_leg(
            db=s, leg=leg, cargo_t=Decimal("100"), distance_nm=Decimal("200")
        )


def test_carbon_intensities():
    r = asyncio.run(_scenario())
    # DO agrégé depuis les noon reports
    assert r.do_consumed_t == Decimal("2.000")
    # CO₂ émis = DO × 3.206 (MEPC.391(81))
    assert r.co2_emitted_t == Decimal("6.412")
    # Intensités
    assert r.co2_per_nm_kg == Decimal("32.060")  # 6.412 t ×1000 / 200 NM
    assert r.co2_per_t_kg == Decimal("64.120")  # /100 t
    assert r.co2_per_tnm_g == Decimal("320.600")  # 6.412 ×1e6 / (100×200)
    assert r.do_co2_factor == Decimal("3.206")
