"""G1 — Rappel R27 (approche de bascule d'année civile) : tests unitaires.

Couvre ``app.services.cutoff_reminders`` : sélection des voyages actifs
approchant une bascule d'année (fenêtre ``rappel_cutoff_avant_j``, défaut 7 j)
sans événement Cut-off finalisé à cette date, notification nominative de
chaque utilisateur assigné au navire, et **idempotence** (un 2e passage ne
recrée aucune notification). Moteur SQLite en mémoire (FK activées).
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
from app.models.nav_event import CutoffEvent
from app.models.notification import Notification
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.services import cutoff_reminders
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


async def _base(db, *, atd, ata=None):
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    p1 = Port(name="Fecamp", country="FR", locode="FRFEC")
    p2 = Port(name="Santos", country="BR", locode="BRSSZ")
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code="1AFRBR6",
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd=atd,
        eta=atd + timedelta(days=20),
        etd_ref=atd,
        eta_ref=atd + timedelta(days=20),
        atd=atd,
        ata=ata,
    )
    db.add(leg)
    master = User(
        username="master",
        email="m@t.test",
        hashed_password="x",
        role="marins",
        assigned_vessel_id=vessel.id,
    )
    db.add(master)
    await db.flush()
    return vessel, leg, master


@pytest.mark.asyncio
async def test_select_upcoming_cutoffs_within_window(db):
    now = datetime(2026, 12, 27, tzinfo=UTC)
    # Bascule le 01/01/2027 — dans 5 j, sous la fenêtre par défaut (7 j).
    _, leg, _ = await _base(db, atd=datetime(2026, 12, 20, tzinfo=UTC))

    upcoming = await cutoff_reminders.select_upcoming_cutoffs(db, now)
    assert len(upcoming) == 1
    assert upcoming[0].leg.id == leg.id
    assert upcoming[0].boundary == datetime(2027, 1, 1)


@pytest.mark.asyncio
async def test_select_upcoming_cutoffs_outside_window_ignored(db):
    now = datetime(2026, 12, 1, tzinfo=UTC)
    # Bascule le 01/01/2027 — dans 31 j, hors fenêtre par défaut (7 j).
    await _base(db, atd=datetime(2026, 11, 25, tzinfo=UTC))

    upcoming = await cutoff_reminders.select_upcoming_cutoffs(db, now)
    assert upcoming == []


@pytest.mark.asyncio
async def test_select_upcoming_cutoffs_ignores_already_crossed_boundary(db):
    """La bascule déjà franchie est le ressort de R27 (bloquant), pas du
    rappel d'approche — pas de double alerte."""
    now = datetime(2027, 1, 3, tzinfo=UTC)
    await _base(db, atd=datetime(2026, 12, 20, tzinfo=UTC))

    upcoming = await cutoff_reminders.select_upcoming_cutoffs(db, now)
    assert upcoming == []


@pytest.mark.asyncio
async def test_select_upcoming_cutoffs_skips_leg_with_finalized_cutoff(db):
    now = datetime(2026, 12, 27, tzinfo=UTC)
    vessel, leg, _ = await _base(db, atd=datetime(2026, 12, 20, tzinfo=UTC))
    cutoff = CutoffEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=datetime(2027, 1, 1, tzinfo=UTC),
    )
    db.add(cutoff)
    await db.flush()

    upcoming = await cutoff_reminders.select_upcoming_cutoffs(db, now)
    assert upcoming == []


@pytest.mark.asyncio
async def test_run_reminders_notifies_assigned_master_and_is_idempotent(db):
    now = datetime(2026, 12, 27, tzinfo=UTC)
    vessel, leg, master = await _base(db, atd=datetime(2026, 12, 20, tzinfo=UTC))

    summary = await cutoff_reminders.run_cutoff_reminders(db, now)
    assert summary == {"scanned": 1, "notified": 1}

    notif = (
        await db.execute(select(Notification).where(Notification.target_user_id == master.id))
    ).scalar_one()
    assert notif.link == f"/onboard/events/new/cutoff?leg_id={leg.id}"
    assert notif.type == "info"
    assert leg.leg_code in notif.title

    # 2e passage (même horloge) → aucune nouvelle notification.
    summary2 = await cutoff_reminders.run_cutoff_reminders(db, now)
    assert summary2 == {"scanned": 1, "notified": 0}
    count = (await db.execute(select(func.count()).select_from(Notification))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_run_reminders_no_master_assigned_creates_nothing(db):
    """Aucun utilisateur assigné au navire → best-effort, pas d'erreur, pas
    de notification orpheline."""
    vessel = Vessel(code="ANE", name="Anemos")
    p1 = Port(name="Fecamp", country="FR", locode="FRFEC")
    p2 = Port(name="Santos", country="BR", locode="BRSSZ")
    db.add_all([vessel, p1, p2])
    await db.flush()
    now = datetime(2026, 12, 27, tzinfo=UTC)
    leg = Leg(
        leg_code="1AFRBR6",
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd=datetime(2026, 12, 20, tzinfo=UTC),
        eta=datetime(2027, 1, 10, tzinfo=UTC),
        etd_ref=datetime(2026, 12, 20, tzinfo=UTC),
        eta_ref=datetime(2027, 1, 10, tzinfo=UTC),
        atd=datetime(2026, 12, 20, tzinfo=UTC),
    )
    db.add(leg)
    await db.flush()

    summary = await cutoff_reminders.run_cutoff_reminders(db, now)
    assert summary == {"scanned": 1, "notified": 0}
