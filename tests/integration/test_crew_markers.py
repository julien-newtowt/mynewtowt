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


def test_crew_template_has_markers():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/crew/index.html")[0]
    assert "foreigner_ids" in src
    assert "étranger" in src
    assert "embarked_days" in src
