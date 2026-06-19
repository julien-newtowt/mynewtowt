"""Tests d'intégration des routes RH (session réelle, FK activées).

Cible les comportements que les tests unitaires ne couvrent pas : gardes de
suppression liées aux FK (bug critique remonté en revue), persistance des
créations, unicité du matricule, cycle d'absence et scoping self-service.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.hr_absence import HrAbsence
from app.models.user import User
from app.routers.rh_router import (
    _my_employee,
    absence_create,
    absence_decide,
    contract_delete,
    employee_create,
    employee_delete,
    self_absence_cancel,
)

from .conftest import FakeRequest


async def _employee(db, matricule="E1", **kw) -> Employee:
    emp = Employee(matricule=matricule, first_name="A", last_name="B", **kw)
    db.add(emp)
    await db.flush()
    return emp


async def _contract(db, employee_id, **kw) -> EmploymentContract:
    base = {"contract_type": "cdi", "start_date": date(2020, 1, 1), "status": "active"}
    base.update(kw)
    c = EmploymentContract(employee_id=employee_id, **base)
    db.add(c)
    await db.flush()
    return c


# ── La FK est bien appliquée (sinon les gardes seraient inutiles) ───────
async def test_foreign_keys_are_enforced(db):
    db.add(HrAbsence(employee_id=999, kind="cp", start_date=date(2026, 1, 1),
                     end_date=date(2026, 1, 2), business_days=1))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ── Suppression employé : gardes FK ─────────────────────────────────────
async def test_employee_delete_blocked_with_contract(db, staff_user):
    emp = await _employee(db)
    await _contract(db, emp.id)
    resp = await employee_delete(emp.id, FakeRequest(), db, staff_user)
    assert resp.status_code == 303
    assert "err=deps" in resp.headers["location"]
    assert await db.get(Employee, emp.id) is not None  # toujours là


async def test_employee_delete_blocked_with_subordinate(db, staff_user):
    boss = await _employee(db, "BOSS")
    await _employee(db, "SUB", manager_id=boss.id)
    resp = await employee_delete(boss.id, FakeRequest(), db, staff_user)
    assert "err=deps" in resp.headers["location"]
    assert await db.get(Employee, boss.id) is not None


async def test_employee_delete_ok_when_no_dependents(db, staff_user):
    emp = await _employee(db, "SOLO")
    resp = await employee_delete(emp.id, FakeRequest(), db, staff_user)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/rh/employees"
    assert await db.get(Employee, emp.id) is None


# ── Suppression contrat : garde avenant ─────────────────────────────────
async def test_contract_delete_blocked_with_amendment(db, staff_user):
    emp = await _employee(db)
    base = await _contract(db, emp.id)
    await _contract(db, emp.id, parent_contract_id=base.id, is_amendment=True)
    resp = await contract_delete(base.id, FakeRequest(), db, staff_user)
    assert "err=contract_deps" in resp.headers["location"]
    assert await db.get(EmploymentContract, base.id) is not None


async def test_contract_delete_ok_without_amendment(db, staff_user):
    emp = await _employee(db)
    c = await _contract(db, emp.id)
    resp = await contract_delete(c.id, FakeRequest(), db, staff_user)
    assert resp.status_code == 303
    assert await db.get(EmploymentContract, c.id) is None


# ── Création employé : persistance + unicité matricule ──────────────────
async def test_employee_create_persists(db, staff_user):
    form = {"matricule": "NEW1", "first_name": "Jean", "last_name": "Bon",
            "department": "Commercial"}
    resp = await employee_create(FakeRequest(form), db, staff_user)
    assert resp.status_code == 303
    row = (await db.execute(select(Employee).where(Employee.matricule == "NEW1"))).scalar_one()
    assert row.full_name == "Jean Bon"


async def test_employee_create_duplicate_matricule_400(db, staff_user):
    await _employee(db, "DUP")
    form = {"matricule": "DUP", "first_name": "X", "last_name": "Y"}
    with pytest.raises(HTTPException) as exc:
        await employee_create(FakeRequest(form), db, staff_user)
    assert exc.value.status_code == 400


# ── Absences : création RH + décision ───────────────────────────────────
async def test_absence_create_approved_and_reject(db, staff_user):
    emp = await _employee(db)
    form = {"employee_id": str(emp.id), "kind": "cp", "status": "approved",
            "start_date": "2026-06-15", "end_date": "2026-06-19"}
    await absence_create(FakeRequest(form), db, staff_user)
    absence = (await db.execute(select(HrAbsence))).scalar_one()
    assert absence.status == "approved"
    assert absence.business_days == 5
    assert absence.decided_at is not None

    resp = await absence_decide(absence.id, FakeRequest(), "rejected", db, staff_user)
    assert resp.status_code == 303
    assert (await db.get(HrAbsence, absence.id)).status == "rejected"


# ── Self-service : scoping strict par user_id ───────────────────────────
async def test_self_service_scoping(db, staff_user):
    db.add(User(id=2, username="bob", email="bob@example.test",
                hashed_password="x", role="commercial"))
    await db.flush()
    mine = await _employee(db, "MINE", user_id=1)
    other = await _employee(db, "OTHER", user_id=2)

    assert (await _my_employee(db, staff_user)).id == mine.id

    # Une demande appartenant à un AUTRE collaborateur n'est pas annulable.
    foreign = HrAbsence(employee_id=other.id, kind="cp", start_date=date(2026, 1, 1),
                        end_date=date(2026, 1, 2), business_days=1, status="requested")
    db.add(foreign)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await self_absence_cancel(foreign.id, FakeRequest(), db, staff_user)
    assert exc.value.status_code == 404
    assert (await db.get(HrAbsence, foreign.id)).status == "requested"  # intacte
