"""Tests de la cascade automatique des legs aval (scénarios provisoires)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.planning_scenario import PlanningScenario, ScenarioLeg
from app.services import scenario as svc

D0 = datetime(2026, 6, 1, 8, 0)


def _mk_leg(scenario_id, vessel_id, dep, arr, start, dur_days):
    return ScenarioLeg(
        scenario_id=scenario_id,
        vessel_id=vessel_id,
        departure_port_id=dep,
        arrival_port_id=arr,
        etd=start,
        eta=start + timedelta(days=dur_days),
    )


def _run(coro):
    async def _wrap():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                await coro(s)
        finally:
            await eng.dispose()

    asyncio.run(_wrap())


def test_moving_leg_later_cascades_downstream() -> None:
    async def body(s):
        sc = PlanningScenario(name="S")
        s.add(sc)
        await s.flush()
        l1 = _mk_leg(sc.id, 1, 1, 2, D0, 1)  # J0 → J1
        l2 = _mk_leg(sc.id, 1, 2, 3, D0 + timedelta(days=2), 1)  # J2 → J3
        l3 = _mk_leg(sc.id, 1, 3, 4, D0 + timedelta(days=4), 1)  # J4 → J5
        s.add_all([l1, l2, l3])
        await s.flush()

        # Décale L1 de +3 jours → L2 et L3 suivent du même delta.
        await svc.update_scenario_leg(s, l1, etd=D0 + timedelta(days=3), eta=D0 + timedelta(days=4))

        legs = {
            x.departure_port_id: x for x in (await s.execute(select(ScenarioLeg))).scalars().all()
        }
        assert legs[2].etd == D0 + timedelta(days=5)  # L2 décalé +3j
        assert legs[3].etd == D0 + timedelta(days=7)  # L3 décalé +3j
        # Aucun chevauchement.
        assert legs[2].etd >= legs[1].eta
        assert legs[3].etd >= legs[2].eta

    _run(body)


def test_extending_eta_pushes_overlap() -> None:
    async def body(s):
        sc = PlanningScenario(name="S")
        s.add(sc)
        await s.flush()
        l1 = _mk_leg(sc.id, 1, 1, 2, D0, 1)  # J0 → J1
        l2 = _mk_leg(sc.id, 1, 2, 3, D0 + timedelta(days=2), 1)  # J2 → J3
        s.add_all([l1, l2])
        await s.flush()

        # Allonge l'escale/transit de L1 : ETA repoussée à J4 (chevauche L2 à J2).
        await svc.update_scenario_leg(s, l1, eta=D0 + timedelta(days=4))

        l2b = (
            await s.execute(select(ScenarioLeg).where(ScenarioLeg.departure_port_id == 2))
        ).scalar_one()
        # L2 repoussé pour démarrer au plus tôt à la fin de L1 (J4), durée préservée.
        assert l2b.etd >= D0 + timedelta(days=4)
        assert (l2b.eta - l2b.etd) == timedelta(days=1)

    _run(body)


def test_no_cascade_when_only_label_changes() -> None:
    async def body(s):
        sc = PlanningScenario(name="S")
        s.add(sc)
        await s.flush()
        l1 = _mk_leg(sc.id, 1, 1, 2, D0, 1)
        l2 = _mk_leg(sc.id, 1, 2, 3, D0 + timedelta(days=2), 1)
        s.add_all([l1, l2])
        await s.flush()
        l2_etd_before = l2.etd

        await svc.update_scenario_leg(s, l1, label="renommé")

        l2b = (
            await s.execute(select(ScenarioLeg).where(ScenarioLeg.departure_port_id == 2))
        ).scalar_one()
        assert l2b.etd == l2_etd_before  # pas de décalage sur un simple renommage

    _run(body)
