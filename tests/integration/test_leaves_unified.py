"""EVO-02 — service de lecture unifié des congés (CrewLeave + HrAbsence).

Vérifie la fusion en lecture des congés marins et des absences sédentaires
derrière un DTO commun, le mapping des champs (population, nom, jours ouvrés)
et le filtre par statut. Pas de fusion de schéma — chaque table reste distincte.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_list_unified_merges_crew_and_hr(db):
    from app.models.crew import CrewLeave, CrewMember
    from app.models.employee import Employee
    from app.models.hr_absence import HrAbsence
    from app.services import leaves as leaves_svc

    db.add(CrewMember(id=1, full_name="Jean Marin", role="capitaine"))
    db.add(Employee(id=1, matricule="E001", first_name="Marie", last_name="Bureau"))
    await db.flush()
    db.add(
        CrewLeave(
            crew_member_id=1,
            kind="cp",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 10),
            status="approved",
        )
    )
    db.add(
        HrAbsence(
            employee_id=1,
            kind="cp",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
            business_days=Decimal("5.0"),
            status="requested",
        )
    )
    await db.flush()

    rows = await leaves_svc.list_unified(db)
    assert len(rows) == 2
    by_src = {r.source: r for r in rows}
    assert by_src["crew"].population == "marin"
    assert by_src["crew"].person_name == "Jean Marin"
    assert by_src["crew"].business_days is None
    assert by_src["hr"].population == "sédentaire"
    assert by_src["hr"].person_name == "Marie Bureau"
    assert by_src["hr"].business_days == Decimal("5.0")

    assert leaves_svc.summary(rows) == {
        "total": 2,
        "marin": 1,
        "sedentaire": 1,
        "pending": 1,
    }


@pytest.mark.asyncio
async def test_list_unified_status_filter(db):
    from app.models.crew import CrewLeave, CrewMember
    from app.services import leaves as leaves_svc

    db.add(CrewMember(id=1, full_name="Jean Marin", role="capitaine"))
    await db.flush()
    db.add(
        CrewLeave(
            crew_member_id=1,
            kind="cp",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 10),
            status="approved",
        )
    )
    db.add(
        CrewLeave(
            crew_member_id=1,
            kind="rtt",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            status="requested",
        )
    )
    await db.flush()

    approved = await leaves_svc.list_unified(db, status="approved")
    assert len(approved) == 1
    assert approved[0].status == "approved"


def test_unified_route_registered():
    from app.routers import rh_router

    paths = {r.path for r in rh_router.router.routes}
    assert "/rh/conges" in paths
