"""Application d'un scénario au planning réel — gardes du moteur de planification.

``apply_to_active_planning`` est la **seule** fonction du module scénario qui
écrit dans ``legs``, et elle n'avait aucune couverture. Elle posait
``real.etd``/``real.eta`` à la main : elle pouvait donc reculer un leg déjà
appareillé, créer un chevauchement que l'écran de planification refuse, et
n'avertissait ni l'escale, ni les dockers, ni les clients.

Elle délègue désormais à ``planning.update_leg``. Ces tests vérifient qu'elle en
hérite réellement — pas qu'elle a recopié les règles.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.planning_scenario import PlanningScenario, ScenarioLeg
from app.models.port import Port
from app.models.schedule_revision import ScheduleRevision
from app.models.vessel import Vessel
from app.services import scenario as svc
from app.services.planning import ensure_utc

D0 = datetime(2026, 6, 1, tzinfo=UTC)


def _utc(value):
    """SQLite rend les ``DateTime(timezone=True)`` **naïfs** : on recolle l'UTC
    avant de comparer, comme le fait le code applicatif (``ensure_utc``)."""
    return ensure_utc(value)


async def _referentials(db):
    db.add(
        Vessel(
            id=1,
            code="ANE",
            name="Anemos",
            imo_number="9876543",
            flag="FR",
            default_speed_kn=8.0,
            default_elongation=1.15,
            is_active=True,
        )
    )
    db.add_all(
        [
            Port(id=1, locode="FRFEC", name="Fécamp", country="FR", latitude=49.76, longitude=0.37),
            Port(
                id=2,
                locode="BRSSO",
                name="São Sebastião",
                country="BR",
                latitude=-23.80,
                longitude=-45.40,
            ),
        ]
    )
    await db.flush()


async def _real_leg(db, *, leg_id=1, code="1AFRBR6", etd=D0, days=25, **kw):
    leg = Leg(
        id=leg_id,
        leg_code=code,
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=etd,
        eta_ref=etd + timedelta(days=days),
        etd=etd,
        eta=etd + timedelta(days=days),
        port_stay_planned_hours=48,
        **kw,
    )
    db.add(leg)
    await db.flush()
    return leg


async def _scenario_with(db, *, label, etd, days=25, **kw):
    scenario = PlanningScenario(id=1, name="Hypothèse été", status="draft")
    db.add(scenario)
    await db.flush()
    db.add(
        ScenarioLeg(
            scenario_id=scenario.id,
            vessel_id=1,
            departure_port_id=kw.pop("departure_port_id", 1),
            arrival_port_id=kw.pop("arrival_port_id", 2),
            etd=etd,
            eta=etd + timedelta(days=days),
            label=label,
            port_stay_planned_hours=48,
            **kw,
        )
    )
    await db.flush()
    return scenario


# ─────────────────── Un départ réel ne se replanifie pas ───────────────────


@pytest.mark.asyncio
async def test_apply_refuses_to_move_a_leg_that_has_sailed(db):
    """Le cœur du correctif : un ATD est un fait, pas une hypothèse.

    Le moteur réel refuse déjà de déplacer un leg parti
    (``plan_downstream_shifts``). L'application d'un scénario ne doit pas être
    la porte dérobée qui le fait.
    """
    await _referentials(db)
    real = await _real_leg(db, atd=D0 + timedelta(hours=3))
    scenario = await _scenario_with(db, label="1AFRBR6", etd=D0 + timedelta(days=10))

    with pytest.raises(svc.ScenarioError) as exc:
        await svc.apply_to_active_planning(db, scenario)
    assert "appareillé" in str(exc.value)
    assert "1AFRBR6" in str(exc.value)

    await db.refresh(real)
    assert _utc(real.etd) == D0, "aucune écriture avant le refus"


@pytest.mark.asyncio
async def test_apply_tolerates_a_sailed_leg_it_does_not_change(db):
    """Un leg appareillé repris à l'identique ne bloque pas l'application.

    Le scénario cloné contient nécessairement les legs en cours ; les refuser
    en bloc rendrait l'outil inutilisable dès qu'un navire est en mer. Seul un
    **déplacement** est refusé.
    """
    await _referentials(db)
    real = await _real_leg(db, atd=D0 + timedelta(hours=3))
    scenario = await _scenario_with(db, label="1AFRBR6", etd=D0)

    result = await svc.apply_to_active_planning(db, scenario)
    assert result.changed_legs == 0
    await db.refresh(real)
    assert _utc(real.etd) == D0


# ─────────────────── Les gardes du moteur réel s'appliquent ─────────────────


@pytest.mark.asyncio
async def test_apply_refuses_a_towt_archive(db):
    """Archive TOWT (ADR-014) : lecture seule, y compris par ce chemin."""
    await _referentials(db)
    await _real_leg(db, code="1YMB4", origin=LEG_ORIGIN_TOWT)
    scenario = await _scenario_with(db, label="1YMB4", etd=D0 + timedelta(days=5))

    from app.services.planning import LegArchivedError

    with pytest.raises((LegArchivedError, svc.ScenarioError)):
        await svc.apply_to_active_planning(db, scenario)


@pytest.mark.asyncio
async def test_apply_moves_a_planned_leg_and_historises_it(db):
    """Cas nominal : un leg encore planifié se déplace, et la trace est posée.

    L'historisation vient de ``update_leg`` (``schedule_revisions``), pas d'un
    appel recopié dans le module scénario.
    """
    await _referentials(db)
    real = await _real_leg(db)
    scenario = await _scenario_with(db, label="1AFRBR6", etd=D0 + timedelta(days=7))

    result = await svc.apply_to_active_planning(db, scenario, user_id=1, user_name="Yasmin")
    assert result.changed_legs == 1
    assert result.batch_id

    await db.refresh(real)
    assert _utc(real.etd) == D0 + timedelta(days=7)

    revisions = (
        (await db.execute(select(ScheduleRevision).where(ScheduleRevision.leg_id == real.id)))
        .scalars()
        .all()
    )
    assert revisions, "le déplacement doit laisser une révision de planning"
    assert any(r.source == "scenario_apply" for r in revisions)


@pytest.mark.asyncio
async def test_apply_is_refused_when_the_scenario_is_archived(db):
    await _referentials(db)
    await _real_leg(db)
    scenario = await _scenario_with(db, label="1AFRBR6", etd=D0 + timedelta(days=7))
    scenario.status = "archived"
    await db.flush()

    with pytest.raises(svc.ScenarioError, match="archivé"):
        await svc.apply_to_active_planning(db, scenario)


@pytest.mark.asyncio
async def test_apply_is_refused_when_a_label_matches_no_real_leg(db):
    await _referentials(db)
    await _real_leg(db)
    scenario = await _scenario_with(db, label="INCONNU", etd=D0 + timedelta(days=7))

    with pytest.raises(svc.ScenarioError, match="INCONNU"):
        await svc.apply_to_active_planning(db, scenario)


# ─────────────────────────── Clone aux dates réelles ────────────────────────


@pytest.mark.asyncio
async def test_clone_uses_effective_dates(db):
    """Cloner un leg parti doit reprendre son **ATD**, pas son ETD prévisionnel.

    Sinon le scénario part d'une image périmée exactement là où elle compte le
    plus — sur le voyage en cours.
    """
    await _referentials(db)
    atd = D0 + timedelta(days=2)
    ata = D0 + timedelta(days=28)
    await _real_leg(db, atd=atd, ata=ata)
    scenario = PlanningScenario(id=1, name="Clone", status="draft")
    db.add(scenario)
    await db.flush()

    cloned = await svc.clone_real_legs_into(db, scenario)
    assert cloned == 1
    sc_leg = (await db.execute(select(ScenarioLeg))).scalars().one()
    assert _utc(sc_leg.etd) == atd, "le départ réel prime sur le prévisionnel"
    assert _utc(sc_leg.eta) == ata, "l'arrivée réelle prime sur la prévisionnelle"


@pytest.mark.asyncio
async def test_clone_falls_back_to_forecast_when_nothing_happened(db):
    await _referentials(db)
    await _real_leg(db)
    scenario = PlanningScenario(id=1, name="Clone", status="draft")
    db.add(scenario)
    await db.flush()

    await svc.clone_real_legs_into(db, scenario)
    sc_leg = (await db.execute(select(ScenarioLeg))).scalars().one()
    assert _utc(sc_leg.etd) == D0
