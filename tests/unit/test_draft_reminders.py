"""LOT 4 — Alerte R19 (brouillons d'événements dormants) : tests unitaires.

Couvre ``app.services.draft_reminders`` : sélection des brouillons par âge vs
les deux seuils R19 (rappel Master 24 h, alerte siège 48 h — défauts codés
sans ligne en base) et **idempotence** (un 2e passage ne recrée aucune
notification). Moteur SQLite en mémoire (FK activées).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre les modèles sur Base.metadata
from app.database import Base
from app.models.leg import Leg
from app.models.nav_event import NoonEvent
from app.models.notification import Notification
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.services import draft_reminders
from app.services.draft_reminders import SIEGE_MRV_ROLES
from app.services.validation_engine import invalidate_cache


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    invalidate_cache()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        invalidate_cache()


async def _base(db):
    author = User(username="master", email="m@t.test", hashed_password="x", role="marins")
    db.add(author)
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    p1 = Port(name="Fecamp", country="FR", locode="FRFEC")
    p2 = Port(name="Santos", country="BR", locode="BRSSZ")
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code="1AFRBR6", vessel_id=vessel.id,
        departure_port_id=p1.id, arrival_port_id=p2.id,
        etd=datetime(2026, 4, 1, tzinfo=UTC), eta=datetime(2026, 4, 20, tzinfo=UTC),
        etd_ref=datetime(2026, 4, 1, tzinfo=UTC), eta_ref=datetime(2026, 4, 20, tzinfo=UTC),
    )
    db.add(leg)
    await db.flush()
    return author, vessel, leg


async def _draft(db, leg, vessel, author, *, hours_old, now):
    ev = NoonEvent(
        leg_id=leg.id, vessel_id=vessel.id, status="brouillon",
        author_user_id=author.id, created_at=now - timedelta(hours=hours_old),
    )
    db.add(ev)
    await db.flush()
    return ev


@pytest.mark.asyncio
async def test_select_dormant_drafts_by_thresholds(db):
    author, vessel, leg = await _base(db)
    now = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    await _draft(db, leg, vessel, author, hours_old=10, now=now)  # < 24 → ignoré
    await _draft(db, leg, vessel, author, hours_old=30, now=now)  # 24..48 → master
    await _draft(db, leg, vessel, author, hours_old=60, now=now)  # > 48 → master + siège

    dormant = await draft_reminders.select_dormant_drafts(db, now)
    assert len(dormant) == 2  # le 10 h est écarté (sous le 1er seuil)
    by_over_siege = sorted(d.over_siege for d in dormant)
    assert by_over_siege == [False, True]  # un seul franchit le 2e seuil
    assert all(d.over_master for d in dormant)


@pytest.mark.asyncio
async def test_run_reminders_notifies_and_is_idempotent(db):
    author, vessel, leg = await _base(db)
    now = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    await _draft(db, leg, vessel, author, hours_old=10, now=now)
    await _draft(db, leg, vessel, author, hours_old=30, now=now)
    await _draft(db, leg, vessel, author, hours_old=60, now=now)

    summary = await draft_reminders.run_draft_reminders(db, now)
    assert summary["scanned"] == 2
    assert summary["master"] == 2  # 30 h + 60 h, notif nominative à l'auteur
    assert summary["siege"] == len(SIEGE_MRV_ROLES)  # seul le 60 h, 1 notif/rôle siège

    async def _count():
        return (
            await db.execute(select(func.count()).select_from(Notification))
        ).scalar_one()

    total_after_first = await _count()
    assert total_after_first == 2 + len(SIEGE_MRV_ROLES)

    # 2e passage (même horloge) → aucune nouvelle notification (idempotence).
    summary2 = await draft_reminders.run_draft_reminders(db, now)
    assert summary2["master"] == 0
    assert summary2["siege"] == 0
    assert await _count() == total_after_first


@pytest.mark.asyncio
async def test_master_notification_targets_author(db):
    author, vessel, leg = await _base(db)
    now = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    ev = await _draft(db, leg, vessel, author, hours_old=30, now=now)

    await draft_reminders.run_draft_reminders(db, now)
    notif = (
        await db.execute(
            select(Notification).where(Notification.target_user_id == author.id)
        )
    ).scalar_one()
    assert notif.link == f"/onboard/events/{ev.id}/edit"
    assert notif.type == "info"


@pytest.mark.asyncio
async def test_no_reminder_below_first_threshold(db):
    author, vessel, leg = await _base(db)
    now = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    await _draft(db, leg, vessel, author, hours_old=5, now=now)

    summary = await draft_reminders.run_draft_reminders(db, now)
    assert summary == {"scanned": 0, "master": 0, "siege": 0}
    assert (
        await db.execute(select(func.count()).select_from(Notification))
    ).scalar_one() == 0
