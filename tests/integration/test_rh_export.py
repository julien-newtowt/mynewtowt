"""Tests d'intégration de l'export Silae (SIRH L5)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.employee import Employee
from app.models.payroll_variable import PayrollVariable
from app.models.silae_export_batch import SilaeExportBatch
from app.routers.rh_router import (
    export_download,
    payroll_add_line,
    payroll_export,
    payroll_lock,
)

from .conftest import FakeRequest

PERIOD = "2026-06"


async def _employee(db, matricule="E1", **kw) -> Employee:
    emp = Employee(matricule=matricule, first_name="Alice", last_name="Martin", **kw)
    db.add(emp)
    await db.flush()
    return emp


async def _locked_line(db, staff_user, emp_id):
    form = {"employee_id": str(emp_id), "evp_type": "heures_supp", "quantity": "3"}
    await payroll_add_line(PERIOD, FakeRequest(form), db, staff_user)
    await payroll_lock(PERIOD, FakeRequest(), db, staff_user)


async def test_export_requires_locked_lines(db, staff_user):
    emp = await _employee(db)
    form = {"employee_id": str(emp.id), "evp_type": "heures_supp", "quantity": "3"}
    await payroll_add_line(PERIOD, FakeRequest(form), db, staff_user)
    # Pas encore verrouillée → export refusé.
    with pytest.raises(HTTPException) as exc:
        await payroll_export(PERIOD, FakeRequest(), db, staff_user)
    assert exc.value.status_code == 400


async def test_export_creates_batch_and_marks_lines(db, staff_user):
    emp = await _employee(db, silae_id="S123")
    await _locked_line(db, staff_user, emp.id)

    resp = await payroll_export(PERIOD, FakeRequest(), db, staff_user)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/rh/exports"

    batch = (await db.execute(select(SilaeExportBatch))).scalar_one()
    assert batch.period == PERIOD
    assert batch.line_count == 1
    assert batch.status == "generated"
    assert "heures_supp" in (batch.content or "")
    assert "S123" in (batch.content or "")

    line = (await db.execute(select(PayrollVariable))).scalar_one()
    assert line.status == "exported"
    assert line.export_batch_id == batch.id


async def test_export_is_idempotent(db, staff_user):
    emp = await _employee(db)
    await _locked_line(db, staff_user, emp.id)
    await payroll_export(PERIOD, FakeRequest(), db, staff_user)

    # Plus aucune ligne « locked » → second export refusé (pas de doublon).
    with pytest.raises(HTTPException) as exc:
        await payroll_export(PERIOD, FakeRequest(), db, staff_user)
    assert exc.value.status_code == 400
    batches = (await db.execute(select(SilaeExportBatch))).scalars().all()
    assert len(batches) == 1


async def test_export_download_returns_csv(db, staff_user):
    emp = await _employee(db)
    await _locked_line(db, staff_user, emp.id)
    await payroll_export(PERIOD, FakeRequest(), db, staff_user)
    batch = (await db.execute(select(SilaeExportBatch))).scalar_one()

    resp = await export_download(batch.id, db, staff_user)
    assert resp.media_type.startswith("text/csv")
    assert b"heures_supp" in resp.body
    assert resp.headers["content-disposition"].endswith(f'{batch.filename}"')
