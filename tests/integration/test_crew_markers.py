"""CREW-09 — marqueur « étranger » (hors Schengen) + jours embarqués / an."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


def test_is_non_schengen_national():
    from app.services.crew_compliance import is_non_schengen_national

    assert is_non_schengen_national("US") is True
    assert is_non_schengen_national("us") is True  # insensible à la casse
    assert is_non_schengen_national("FR") is False  # Schengen
    assert is_non_schengen_national(None) is False  # inconnu → pas de marqueur
    assert is_non_schengen_national("") is False


def test_assignment_days_in_year_clamping():
    from app.services.crew_compliance import assignment_days_in_year

    now = datetime(2026, 6, 30, tzinfo=UTC)
    # Fenêtre entière dans l'année (inclusive).
    assert (
        assignment_days_in_year(
            datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 3, 10, tzinfo=UTC), 2026, now=now
        )
        == 10
    )
    # Toujours à bord (pas de débarquement) → jusqu'à now.
    assert assignment_days_in_year(datetime(2026, 6, 1, tzinfo=UTC), None, 2026, now=now) == 30
    # À cheval sur le 1er janvier → borné au début d'année.
    assert (
        assignment_days_in_year(
            datetime(2025, 12, 20, tzinfo=UTC), datetime(2026, 1, 5, tzinfo=UTC), 2026, now=now
        )
        == 5
    )
    # Entièrement sur une autre année → 0.
    assert (
        assignment_days_in_year(
            datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC), 2026, now=now
        )
        == 0
    )
    # Pas d'embarquement → 0.
    assert assignment_days_in_year(None, None, 2026, now=now) == 0


@pytest.mark.asyncio
async def test_embarked_days_by_member(db):
    from app.models.crew import CrewAssignment, CrewMember
    from app.services.crew_compliance import embarked_days_by_member

    db.add(CrewMember(id=1, full_name="Jean Marin", role="capitaine"))
    db.add(CrewMember(id=2, full_name="Sans Embarquement", role="marin"))
    await db.flush()
    db.add(
        CrewAssignment(
            crew_member_id=1,
            embark_at=datetime(2026, 3, 1, tzinfo=UTC),
            disembark_at=datetime(2026, 3, 10, tzinfo=UTC),
        )
    )
    db.add(
        CrewAssignment(
            crew_member_id=1,
            embark_at=datetime(2026, 5, 1, tzinfo=UTC),
            disembark_at=datetime(2026, 5, 5, tzinfo=UTC),
        )
    )
    await db.flush()

    days = await embarked_days_by_member(db, 2026, now=datetime(2026, 6, 30, tzinfo=UTC))
    assert days.get(1) == 15  # 10 + 5
    assert days.get(2) is None  # aucune affectation


@pytest.mark.asyncio
async def test_embarked_days_includes_marad_and_excludes_leave(db):
    """Les jours embarqués comptent les plannings Marad (avec navire) et
    ignorent les périodes à terre (congés = Vessel null) — sinon l'indicateur
    restait à 0 puisque les marins viennent de Marad (pas de CrewAssignment).

    On identifie l'embarquement par ``marad_vessel_name`` (pas besoin de FK
    navire pour ce test)."""
    from datetime import date

    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.services.crew_compliance import embarked_days_by_member

    db.add(CrewMember(id=10, full_name="Marad Sailor", role="capitaine"))
    await db.flush()
    db.add(
        MaradCrewSchedule(
            marad_schedule_id="e1", crew_member_id=10, marad_vessel_name="Anemos",
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 5),
        )
    )  # embarquement : 5 j
    db.add(
        MaradCrewSchedule(
            marad_schedule_id="e2", crew_member_id=10, start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30), status="Congés",
        )
    )  # congé (pas de navire) : ne compte pas
    await db.flush()
    days = await embarked_days_by_member(db, 2026, now=datetime(2026, 6, 30, tzinfo=UTC))
    assert days.get(10) == 5


@pytest.mark.asyncio
async def test_current_embarkations_window_and_leave_filter(db):
    from datetime import date

    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.services.crew_compliance import current_embarkations

    db.add(CrewMember(id=20, full_name="A Bord", role="second"))
    await db.flush()
    db.add_all([
        MaradCrewSchedule(marad_schedule_id="c1", crew_member_id=20, marad_vessel_name="Anemos",
                          start_date=date(2026, 6, 1), end_date=date(2026, 6, 30)),  # en cours
        MaradCrewSchedule(marad_schedule_id="c2", crew_member_id=20, status="Congés",
                          start_date=date(2026, 6, 1), end_date=date(2026, 6, 30)),  # congé → exclu
        MaradCrewSchedule(marad_schedule_id="c3", crew_member_id=20, marad_vessel_name="Anemos",
                          start_date=date(2026, 1, 1), end_date=date(2026, 1, 10)),  # fini → exclu
    ])
    await db.flush()
    cur = await current_embarkations(db, on=date(2026, 6, 15))
    assert [s.marad_schedule_id for s in cur] == ["c1"]


@pytest.mark.asyncio
async def test_crew_for_leg_by_leg_id(db):
    from datetime import date, timedelta

    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel
    from app.services.crew_compliance import crew_for_leg

    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    db.add(CrewMember(id=30, full_name="Zoe Embarquee", role="marin"))
    await db.flush()
    base = datetime(2026, 3, 1, tzinfo=UTC)
    leg = Leg(id=1, leg_code="1CFRBR6", vessel_id=1, departure_port_id=1, arrival_port_id=2,
              etd_ref=base, eta_ref=base + timedelta(days=9), etd=base, eta=base + timedelta(days=9))
    db.add(leg)
    await db.flush()
    db.add_all([
        MaradCrewSchedule(marad_schedule_id="L1", crew_member_id=30, vessel_id=1, leg_id=leg.id,
                          marad_vessel_name="Anemos", rank_label="Matelot",
                          start_date=date(2026, 3, 2), end_date=date(2026, 3, 9)),
        MaradCrewSchedule(marad_schedule_id="L2", crew_member_id=30, leg_id=leg.id,
                          start_date=date(2026, 3, 2), end_date=date(2026, 3, 9),
                          status="Congés"),  # congé sur le même leg → exclu
    ])
    await db.flush()
    crew = await crew_for_leg(db, leg, 1)
    assert len(crew) == 1
    s, m = crew[0]
    assert m.full_name == "Zoe Embarquee" and s.rank_label == "Matelot"


def test_crew_template_has_markers():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/crew/index.html")[0]
    assert "foreigner_ids" in src
    assert "étranger" in src
    assert "embarked_days" in src
