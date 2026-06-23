"""Tests d'intégration des routes EVP / paie (SIRH L4)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.employee import Employee
from app.models.hr_absence import HrAbsence
from app.models.payroll_variable import PayrollVariable
from app.routers.rh_router import (
    payroll_add_line,
    payroll_delete_line,
    payroll_lock,
    payroll_sync_absences,
)

from .conftest import FakeRequest

PERIOD = "2026-06"


async def _employee(db, matricule="E1", **kw) -> Employee:
    emp = Employee(matricule=matricule, first_name="A", last_name="B", **kw)
    db.add(emp)
    await db.flush()
    return emp


async def _approved_absence(db, employee_id, start, end, days, kind="cp") -> HrAbsence:
    ab = HrAbsence(
        employee_id=employee_id,
        kind=kind,
        start_date=start,
        end_date=end,
        business_days=days,
        status="approved",
    )
    db.add(ab)
    await db.flush()
    return ab


async def test_add_line_persists(db, staff_user):
    emp = await _employee(db)
    form = {
        "employee_id": str(emp.id),
        "evp_type": "prime_objectifs",
        "quantity": "1",
        "amount": "500",
        "comment": "Q2",
    }
    resp = await payroll_add_line(PERIOD, FakeRequest(form), db, staff_user)
    assert resp.status_code == 303
    line = (await db.execute(select(PayrollVariable))).scalar_one()
    assert line.evp_type == "prime_objectifs"
    assert line.status == "draft"
    assert line.source == "manual"


async def test_add_line_invalid_type_400(db, staff_user):
    emp = await _employee(db)
    form = {"employee_id": str(emp.id), "evp_type": "bonus_inconnu", "quantity": "1"}
    with pytest.raises(HTTPException) as exc:
        await payroll_add_line(PERIOD, FakeRequest(form), db, staff_user)
    assert exc.value.status_code == 400


async def test_sync_absences_is_idempotent(db, staff_user):
    emp = await _employee(db)
    await _approved_absence(db, emp.id, date(2026, 6, 15), date(2026, 6, 19), 5)
    # Une absence hors période ne doit pas être importée.
    await _approved_absence(db, emp.id, date(2026, 7, 1), date(2026, 7, 3), 3)

    await payroll_sync_absences(PERIOD, FakeRequest(), db, staff_user)
    rows = (await db.execute(select(PayrollVariable))).scalars().all()
    assert len(rows) == 1
    assert rows[0].evp_type == "absence"
    assert rows[0].quantity == 5
    assert rows[0].source == "absence"

    # Second passage : pas de doublon (déduplication par absence_id).
    await payroll_sync_absences(PERIOD, FakeRequest(), db, staff_user)
    rows = (await db.execute(select(PayrollVariable))).scalars().all()
    assert len(rows) == 1


async def test_lock_freezes_lines_and_blocks_mutations(db, staff_user):
    emp = await _employee(db)
    form = {"employee_id": str(emp.id), "evp_type": "heures_supp", "quantity": "3"}
    await payroll_add_line(PERIOD, FakeRequest(form), db, staff_user)

    resp = await payroll_lock(PERIOD, FakeRequest(), db, staff_user)
    assert resp.status_code == 303
    line = (await db.execute(select(PayrollVariable))).scalar_one()
    assert line.status == "locked"

    # Ajout interdit sur période verrouillée.
    with pytest.raises(HTTPException) as exc:
        await payroll_add_line(PERIOD, FakeRequest(form), db, staff_user)
    assert exc.value.status_code == 400

    # Suppression d'une ligne verrouillée interdite.
    with pytest.raises(HTTPException) as exc:
        await payroll_delete_line(PERIOD, line.id, FakeRequest(), db, staff_user)
    assert exc.value.status_code == 400


async def test_lock_empty_period_400(db, staff_user):
    with pytest.raises(HTTPException) as exc:
        await payroll_lock(PERIOD, FakeRequest(), db, staff_user)
    assert exc.value.status_code == 400
