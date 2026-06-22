"""Tests de l'agrégation sinistralité (reporting claims — E8)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.claim import Claim
from app.routers.claims_router import _claims_stats


def test_claims_stats_aggregates() -> None:
    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                now = datetime.now(UTC)
                s.add_all(
                    [
                        Claim(
                            reference="CLM-2026-0001", claim_type="cargo", title="A",
                            description="x", status="settled", occurred_at=now,
                            declared_at=now - timedelta(days=10), settled_at=now,
                            provision_eur=Decimal("1000"), settled_eur=Decimal("800"),
                        ),
                        Claim(
                            reference="CLM-2026-0002", claim_type="hull", title="B",
                            description="y", status="open", occurred_at=now,
                            declared_at=now, provision_eur=Decimal("500"),
                        ),
                    ]
                )
                await s.flush()

                stats = await _claims_stats(s)
                assert stats["total"] == 2
                assert stats["by_type"]["cargo"] == 1
                assert stats["by_type"]["hull"] == 1
                assert stats["by_status"]["settled"] == 1
                assert stats["by_status"]["open"] == 1
                assert stats["total_provision"] == Decimal("1500")
                assert stats["total_settled"] == Decimal("800")
                # Un seul sinistre réglé, déclaré 10 j avant règlement.
                assert stats["avg_settle_days"] == 10.0
        finally:
            await eng.dispose()

    asyncio.run(_run())
