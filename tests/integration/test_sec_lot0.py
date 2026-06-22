"""Lot 0 — sécurité & intégrité (tests d'intégration, session DB).

Couvre :
- SEC-01 : contrat de rate-limit du login mot de passe (scope/params).
- SEC-02 : 429 du portail token une fois la limite atteinte.
- SEC-04 : contrainte d'unicité (vessel_id, recorded_at) des positions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.claim import VesselPosition
from app.models.vessel import Vessel
from app.services import rate_limit


# ─────────────────────────────── SEC-01 ───────────────────────────────


@pytest.mark.asyncio
async def test_staff_login_rate_limit_contract(db):
    ip = "203.0.113.7"
    scope = "staff_login_ip"
    # 4 échecs : pas encore bloqué.
    for _ in range(4):
        await rate_limit.record(db, scope=scope, identifier=ip)
    assert (
        await rate_limit.exceeded(db, scope=scope, identifier=ip, max_attempts=5, window_minutes=15)
        is False
    )
    # 5e échec : la requête suivante est bloquée.
    await rate_limit.record(db, scope=scope, identifier=ip)
    assert (
        await rate_limit.exceeded(db, scope=scope, identifier=ip, max_attempts=5, window_minutes=15)
        is True
    )
    # Cloisonnement par IP : une autre IP n'est pas affectée.
    assert (
        await rate_limit.exceeded(
            db, scope=scope, identifier="198.51.100.1", max_attempts=5, window_minutes=15
        )
        is False
    )


# ─────────────────────────────── SEC-02 ───────────────────────────────


@pytest.mark.asyncio
async def test_portal_token_rate_limited(db):
    from fastapi import HTTPException

    from app.routers import cargo_portal_router

    class _Req:
        headers: dict[str, str] = {}
        client = type("C", (), {"host": "192.0.2.55"})()

    ip = "192.0.2.55"
    for _ in range(60):
        await rate_limit.record(db, scope="portal_token", identifier=ip)

    with pytest.raises(HTTPException) as exc:
        await cargo_portal_router._load_or_410(db, "any-token", _Req())
    assert exc.value.status_code == 429


# ─────────────────────────────── SEC-04 ───────────────────────────────


@pytest.mark.asyncio
async def test_vessel_position_unique_constraint(db):
    db.add(Vessel(id=1, code="AAA", name="Test Vessel"))
    await db.flush()
    t = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    db.add(VesselPosition(vessel_id=1, recorded_at=t, latitude=49.0, longitude=-1.0))
    await db.flush()
    db.add(VesselPosition(vessel_id=1, recorded_at=t, latitude=49.1, longitude=-1.1))
    with pytest.raises(IntegrityError):
        await db.flush()
