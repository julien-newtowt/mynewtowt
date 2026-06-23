"""Tests de l'historisation météo (snapshot dernier point GPS + idempotence)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.models.weather import VesselWeather
from app.services import weather as wx
from app.services import weather_history as wh


def _fake_point(lat, lon, when):
    return wx.WeatherPoint(
        time=when.isoformat(),
        wind_speed_kn=18.0,
        wind_direction_deg=270.0,
        wave_height_m=2.1,
        wave_direction_deg=280.0,
        wave_period_s=8.0,
        temperature_c=14.5,
        current_speed_kn=0.7,
        current_direction_deg=200.0,
        pressure_hpa=1013.0,
        visibility_km=24.0,
        humidity_pct=72.0,
        cloud_cover_pct=40.0,
    )


def test_beaufort_scale() -> None:
    assert wx.beaufort(None) is None
    assert wx.beaufort(0)[0] == 0
    assert wx.beaufort(22)[0] == 6  # vent frais
    assert wx.beaufort(70)[0] == 12  # ouragan
    assert wx.compass(315) == "NW"
    assert wx.compass(None) == ""


def test_snapshot_latest_is_idempotent(monkeypatch) -> None:
    async def _fake_fetch(lat, lon, when, *, provider="windy"):
        return _fake_point(lat, lon, when)

    monkeypatch.setattr(wx, "fetch_point_conditions", _fake_fetch)

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                v = Vessel(code="ANE", name="Anemos")
                v2 = Vessel(code="GRA", name="Grain de Sail")
                s.add_all([v, v2])
                await s.flush()
                base = datetime(2026, 3, 1, 12, tzinfo=UTC)
                # v a 2 positions (la plus récente sera snapshotée), v2 aucune
                s.add_all(
                    [
                        VesselPosition(
                            vessel_id=v.id,
                            recorded_at=base,
                            latitude=49.0,
                            longitude=-2.0,
                            source="t",
                        ),
                        VesselPosition(
                            vessel_id=v.id,
                            recorded_at=base + timedelta(hours=1),
                            latitude=49.2,
                            longitude=-2.1,
                            source="t",
                        ),
                    ]
                )
                await s.flush()

                r1 = await wh.snapshot_latest(s)
                assert r1["saved"] == 1  # v seulement (v2 sans position → skip)
                assert r1["skipped"] == 1

                obs = (await s.execute(select(VesselWeather))).scalars().all()
                assert len(obs) == 1
                o = obs[0]
                assert o.vessel_id == v.id
                # SQLite ne conserve pas le tzinfo (Postgres oui) → compare naïf
                expected = base + timedelta(hours=1)
                assert o.recorded_at.replace(tzinfo=None) == expected.replace(tzinfo=None)
                assert o.latitude == 49.2
                assert o.wind_speed_kn == 18.0
                assert o.temperature_c == 14.5
                assert o.current_speed_kn == 0.7
                assert o.pressure_hpa == 1013.0
                assert o.visibility_km == 24.0
                assert o.humidity_pct == 72.0

                # 2e passage sans nouveau point → rien de sauvé (idempotent)
                r2 = await wh.snapshot_latest(s)
                assert r2["saved"] == 0
                count = (await s.execute(select(func.count()).select_from(VesselWeather))).scalar()
                assert count == 1

                # nouveau point → nouvel historique
                s.add(
                    VesselPosition(
                        vessel_id=v.id,
                        recorded_at=base + timedelta(hours=2),
                        latitude=49.4,
                        longitude=-2.2,
                        source="t",
                    )
                )
                await s.flush()
                r3 = await wh.snapshot_latest(s)
                assert r3["saved"] == 1
                count = (await s.execute(select(func.count()).select_from(VesselWeather))).scalar()
                assert count == 2
        finally:
            await eng.dispose()

    asyncio.run(_run())


def test_observations_for_leg_window() -> None:
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
                base = datetime(2026, 3, 1, tzinfo=UTC)
                leg = Leg(
                    leg_code="1X",
                    vessel_id=v.id,
                    departure_port_id=1,
                    arrival_port_id=2,
                    etd=base,
                    eta=base + timedelta(days=3),
                    etd_ref=base,
                    eta_ref=base + timedelta(days=3),
                    atd=base,
                    ata=base + timedelta(days=3),
                )
                s.add(leg)
                s.add_all(
                    [
                        VesselWeather(
                            vessel_id=v.id,
                            recorded_at=base - timedelta(hours=2),
                            latitude=1,
                            longitude=1,
                        ),  # hors fenêtre
                        VesselWeather(
                            vessel_id=v.id,
                            recorded_at=base + timedelta(hours=5),
                            latitude=1,
                            longitude=1,
                        ),
                        VesselWeather(
                            vessel_id=v.id,
                            recorded_at=base + timedelta(days=2),
                            latitude=1,
                            longitude=1,
                        ),
                    ]
                )
                await s.flush()
                obs = await wh.observations_for_leg(s, leg)
                assert len(obs) == 2
        finally:
            await eng.dispose()

    asyncio.run(_run())
