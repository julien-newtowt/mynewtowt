"""Archives TOWT (ADR-014) — filets de sécurité au-delà des gardes de service.

La garde ORM refuse tout UPDATE/DELETE d'un leg d'archive, quel que soit
l'écrivain ; la séquence vivante (chevauchement, continuité, legs voisins) et
les indicateurs publiés ignorent l'archive ; le filtre transverse l'exclut par
défaut ; les scénarios ne la clonent pas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.leg import LEG_ARCHIVE_WRITE_KEY, LEG_ORIGIN_TOWT, Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.planning import LegArchivedError, validate_leg_schedule

D = datetime(2026, 1, 13, tzinfo=UTC)


async def _setup(db):
    db.add(Vessel(id=1, code="1", name="Anemos"))
    db.add(
        Port(id=1, locode="USNYC", name="New York", country="US", latitude=40.7, longitude=-74.0)
    )
    db.add(Port(id=2, locode="FRFEC", name="Fécamp", country="FR", latitude=49.76, longitude=0.37))
    db.add(
        Port(
            id=3,
            locode="BRSSO",
            name="São Sebastião",
            country="BR",
            latitude=-23.8,
            longitude=-45.4,
        )
    )
    await db.flush()
    archive = Leg(
        id=1,
        leg_code="1AYF6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=D,
        eta_ref=D + timedelta(days=18),
        etd=D,
        eta=D + timedelta(days=18),
        atd=D,
        ata=D + timedelta(days=18),
        status="completed",
        origin=LEG_ORIGIN_TOWT,
        voyage_completed_at=D + timedelta(days=18),
    )
    db.add(archive)
    await db.flush()
    return archive


@pytest.mark.asyncio
async def test_orm_guard_refuses_direct_update(db):
    archive = await _setup(db)
    archive.eta = D + timedelta(days=20)  # écrivain « oublié » (scénario, ETA bord…)
    with pytest.raises(LegArchivedError):
        await db.flush()


@pytest.mark.asyncio
async def test_orm_guard_refuses_delete(db):
    archive = await _setup(db)
    await db.delete(archive)
    with pytest.raises(LegArchivedError):
        await db.flush()


@pytest.mark.asyncio
async def test_orm_guard_escape_hatch_for_scripts(db):
    archive = await _setup(db)
    db.sync_session.info[LEG_ARCHIVE_WRITE_KEY] = True
    try:
        archive.closure_notes = "correction documentée"
        await db.flush()
    finally:
        db.sync_session.info.pop(LEG_ARCHIVE_WRITE_KEY, None)
    assert (await db.get(Leg, 1)).closure_notes == "correction documentée"


@pytest.mark.asyncio
async def test_live_sequence_ignores_archive(db):
    await _setup(db)
    # Un leg NEWTOWT qui chevauche l'archive et repart d'un autre port :
    # ni LegOverlap ni LegContinuityError — l'archive n'est pas la séquence vivante.
    warnings = await validate_leg_schedule(
        db,
        vessel_id=1,
        departure_port_id=3,
        arrival_port_id=2,
        etd=D + timedelta(days=10),
        eta=D + timedelta(days=40),
    )
    assert isinstance(warnings, list)

    from app.services.voyage_transitions import _next_leg, _previous_legs

    live = Leg(
        id=2,
        leg_code="1AFRBR6",
        vessel_id=1,
        departure_port_id=2,
        arrival_port_id=3,
        etd_ref=D + timedelta(days=60),
        eta_ref=D + timedelta(days=90),
        etd=D + timedelta(days=60),
        eta=D + timedelta(days=90),
    )
    db.add(live)
    await db.flush()
    assert await _previous_legs(db, live) == []
    archive = await db.get(Leg, 1)
    assert await _next_leg(db, archive) is None or (await _next_leg(db, archive)).id == 2


@pytest.mark.asyncio
async def test_published_indicators_exclude_archive(db):
    from app.services import service_reliability, social_proof

    await _setup(db)
    service_reliability._overall_cache = None
    stats = await service_reliability.overall(db)
    assert stats.completed == 0  # l'archive (prévu = réel) ne compte pas comme « tenue »
    route = await service_reliability.for_route(db, 1, 2)
    assert route.completed == 0
    social_proof._counters_cache = None
    counters = await social_proof.counters(db)
    assert counters.crossings == 0


@pytest.mark.asyncio
async def test_leg_filter_excludes_archive_by_default(db):
    from app.services.leg_filter import build_leg_filter, leg_select_options

    await _setup(db)
    f = await build_leg_filter(db, vessel="1", year=2026)
    assert f["legs"] == []
    f2 = await build_leg_filter(db, vessel="1", year=2026, include_archive=True)
    assert [lg.id for lg in f2["legs"]] == [1]
    assert all(o["id"] != 1 for o in await leg_select_options(db))


@pytest.mark.asyncio
async def test_scenario_never_clones_archive(db):
    from app.models.planning_scenario import PlanningScenario
    from app.services.scenario import clone_real_legs_into

    await _setup(db)
    sc = PlanningScenario(name="what-if", created_by_id=1)
    db.add(sc)
    await db.flush()
    n = await clone_real_legs_into(db, sc, vessel_id=1)
    assert n == 0


@pytest.mark.asyncio
async def test_mrv_nightly_run_ignores_archive(db):
    from app.services import kpi_env, validation_rules_catalog

    archive = await _setup(db)
    assert validation_rules_catalog._leg_is_active(archive) is False
    assert kpi_env._leg_is_active(archive) is False
