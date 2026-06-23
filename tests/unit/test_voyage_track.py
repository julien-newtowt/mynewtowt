"""Tests du service voyage_track — fenêtre leg, distances, durée, échantillonnage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (enregistre les modèles)
from app.database import Base
from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services import voyage_track as vt


def _mk_pos(vessel_id, when, lat, lon):
    return VesselPosition(
        vessel_id=vessel_id, recorded_at=when, latitude=lat, longitude=lon, source="test"
    )


def test_leg_window_active_vs_terminated() -> None:
    base = datetime(2026, 3, 1, 8, tzinfo=UTC)
    etd = base
    eta = base + timedelta(days=10)
    # Leg actif (pas d'ATA) : end ≈ now, is_active True.
    leg_active = Leg(
        leg_code="1X",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd=etd,
        eta=eta,
        etd_ref=etd,
        eta_ref=eta,
        atd=base,
    )
    now = base + timedelta(days=3)
    start, end, active = vt.leg_window(leg_active, now=now)
    assert start == base and active is True and end == now

    # Leg terminé (ATD + ATA) : fenêtre exacte, is_active False.
    leg_done = Leg(
        leg_code="2X",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd=etd,
        eta=eta,
        etd_ref=etd,
        eta_ref=eta,
        atd=base,
        ata=base + timedelta(days=9),
    )
    start, end, active = vt.leg_window(leg_done, now=now)
    assert start == base and end == base + timedelta(days=9) and active is False


def test_actual_distance_sums_segments() -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    pts = [
        _mk_pos(1, base, 49.0, -1.0),
        _mk_pos(1, base + timedelta(hours=1), 49.0, 0.0),
        _mk_pos(1, base + timedelta(hours=2), 50.0, 0.0),
    ]
    d = vt.actual_distance_nm(pts)
    # ~39 NM (1° lon @49°N) + 60 NM (1° lat) ≈ 99 NM
    assert 90 < d < 110, d
    assert vt.actual_distance_nm([]) == 0.0
    assert vt.actual_distance_nm([pts[0]]) == 0.0


def test_compute_metrics_active_leg() -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    leg = Leg(
        leg_code="1CFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd=base,
        eta=base + timedelta(days=5),
        etd_ref=base,
        eta_ref=base + timedelta(days=5),
        atd=base,
        distance_nm=Decimal("100"),
    )
    arr = Port(name="Dakar", locode="SNDKR", country="SN", latitude=51.0, longitude=0.0)
    pts = [
        _mk_pos(1, base, 49.0, 0.0),
        _mk_pos(1, base + timedelta(hours=12), 50.0, 0.0),
    ]
    m = vt.compute_metrics(pts, leg, arr_port=arr)
    assert m.point_count == 2
    assert m.actual_nm > 0
    assert m.theoretical_nm == 100.0
    assert m.is_active is True
    # remaining = dernier point (50N) → POD (51N) ≈ 60 NM
    assert 50 < m.remaining_nm < 70, m.remaining_nm
    # durée = dernier point - ATD = 12 h
    assert abs(m.duration_hours - 12.0) < 0.01
    assert m.avg_speed_kn and m.avg_speed_kn > 0


def test_compute_metrics_terminated_remaining_zero() -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    leg = Leg(
        leg_code="2X",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd=base,
        eta=base + timedelta(days=2),
        etd_ref=base,
        eta_ref=base + timedelta(days=2),
        atd=base,
        ata=base + timedelta(days=2),
        distance_nm=Decimal("50"),
    )
    arr = Port(name="X", locode="XXXXX", country="FR", latitude=51.0, longitude=0.0)
    pts = [_mk_pos(1, base, 49.0, 0.0), _mk_pos(1, base + timedelta(days=2), 50.9, 0.0)]
    m = vt.compute_metrics(pts, leg, arr_port=arr)
    assert m.is_active is False
    assert m.remaining_nm == 0.0


def test_downsample_keeps_30min_spacing() -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    pts = [_mk_pos(1, base + timedelta(minutes=10 * i), 49.0 + i * 0.01, 0.0) for i in range(7)]
    # points à 0,10,20,30,40,50,60 min → garde 0, 30, 60
    sampled = vt.downsample_for_weather(pts, minutes=30)
    mins = [int((p.recorded_at - base).total_seconds() // 60) for p in sampled]
    assert mins == [0, 30, 60], mins
    assert vt.downsample_for_weather([]) == []


def test_positions_for_leg_db() -> None:
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
                # 1 point avant la fenêtre, 2 dedans, 1 après
                s.add_all(
                    [
                        _mk_pos(v.id, base - timedelta(hours=1), 49.0, 0.0),
                        _mk_pos(v.id, base + timedelta(hours=6), 49.5, 0.0),
                        _mk_pos(v.id, base + timedelta(days=2), 50.0, 0.0),
                        _mk_pos(v.id, base + timedelta(days=4), 50.5, 0.0),
                    ]
                )
                await s.flush()
                pts = await vt.positions_for_leg(s, leg)
                assert len(pts) == 2
                # tri chronologique
                assert pts[0].recorded_at < pts[1].recorded_at
        finally:
            await eng.dispose()

    asyncio.run(_run())
