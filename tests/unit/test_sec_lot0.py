"""Lot 0 — sécurité & intégrité (tests unitaires).

Couvre :
- SEC-05 : filtre anti-saut de ``voyage_track.actual_distance_nm``.
- SEC-06 : dépendance d'auth ``api_v1_router.require_api_key``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import voyage_track as vt


def _pos(t: datetime, lat: float, lon: float) -> SimpleNamespace:
    return SimpleNamespace(recorded_at=t, latitude=lat, longitude=lon, sog_kn=None, cog_deg=None)


# ─────────────────────────────── SEC-05 ───────────────────────────────


def test_actual_distance_unfiltered_sums_all_segments():
    base = datetime(2026, 3, 1, tzinfo=UTC)
    # Deux points à 1 h d'intervalle, 1° de latitude ≈ 60 NM → 60 kn (irréaliste
    # mais doit être compté quand AUCUN filtre n'est demandé : rétro-compat).
    pts = [_pos(base, 49.0, 0.0), _pos(base + timedelta(hours=1), 50.0, 0.0)]
    d = vt.actual_distance_nm(pts)
    assert 55 < d < 65, d


def test_actual_distance_filters_aberrant_jump():
    base = datetime(2026, 3, 1, tzinfo=UTC)
    # Saut de ~3000 NM en 1 h (point GPS corrompu) → exclu avec le filtre.
    pts = [_pos(base, 49.0, 0.0), _pos(base + timedelta(hours=1), 49.0, 60.0)]
    assert vt.actual_distance_nm(pts, max_speed_kn=vt.MAX_PLAUSIBLE_SPEED_KN) == 0.0


def test_actual_distance_keeps_realistic_segment_when_filtering():
    base = datetime(2026, 3, 1, tzinfo=UTC)
    # 60 NM en 12 h = 5 kn (réaliste pour un voilier-cargo) → conservé.
    pts = [_pos(base, 49.0, 0.0), _pos(base + timedelta(hours=12), 50.0, 0.0)]
    d = vt.actual_distance_nm(pts, max_speed_kn=vt.MAX_PLAUSIBLE_SPEED_KN)
    assert 55 < d < 65, d


def test_actual_distance_zero_duration_segment_filtered():
    base = datetime(2026, 3, 1, tzinfo=UTC)
    pts = [_pos(base, 49.0, 0.0), _pos(base, 49.5, 0.0)]  # même instant, distance > 0
    assert vt.actual_distance_nm(pts, max_speed_kn=vt.MAX_PLAUSIBLE_SPEED_KN) == 0.0


# ─────────────────────────────── SEC-06 ───────────────────────────────


@pytest.mark.asyncio
async def test_require_api_key_unconfigured_returns_503(monkeypatch):
    from app.routers import api_v1_router

    monkeypatch.setattr(api_v1_router.settings, "public_api_key", None)
    with pytest.raises(HTTPException) as exc:
        await api_v1_router.require_api_key(x_api_key="whatever")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_require_api_key_missing_returns_401(monkeypatch):
    from app.routers import api_v1_router

    monkeypatch.setattr(api_v1_router.settings, "public_api_key", "secret-key")
    with pytest.raises(HTTPException) as exc:
        await api_v1_router.require_api_key(x_api_key=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_key_wrong_returns_401(monkeypatch):
    from app.routers import api_v1_router

    monkeypatch.setattr(api_v1_router.settings, "public_api_key", "secret-key")
    with pytest.raises(HTTPException) as exc:
        await api_v1_router.require_api_key(x_api_key="bad")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_key_valid_passes(monkeypatch):
    from app.routers import api_v1_router

    monkeypatch.setattr(api_v1_router.settings, "public_api_key", "secret-key")
    # Ne lève pas.
    assert await api_v1_router.require_api_key(x_api_key="secret-key") is None
