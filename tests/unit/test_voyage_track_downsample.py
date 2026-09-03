"""Décimation d'affichage des traces GPS (archives TOWT au pas de 5 min)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.voyage_track import MAX_TRACK_POINTS_HISTORY, downsample


def _trace(n: int):
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        SimpleNamespace(recorded_at=t0 + timedelta(minutes=5 * i), latitude=float(i), longitude=0.0)
        for i in range(n)
    ]


def test_short_trace_untouched():
    pts = _trace(10)
    assert downsample(pts, max_points=100) == pts
    assert downsample(pts, max_points=0) == pts


def test_long_trace_keeps_ends_and_order():
    pts = _trace(100_000)
    out = downsample(pts, max_points=MAX_TRACK_POINTS_HISTORY)
    assert len(out) == MAX_TRACK_POINTS_HISTORY
    assert out[0] is pts[0] and out[-1] is pts[-1]
    lats = [p.latitude for p in out]
    assert lats == sorted(lats) and len(set(lats)) == len(lats)
