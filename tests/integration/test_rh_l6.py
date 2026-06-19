"""Tests d'intégration L6 — bulletins (coffre-fort), entretiens, reporting."""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from app.models.employee import Employee
from app.models.hr_review import HrReview
from app.models.payslip import Payslip
from app.routers.rh_router import (
    _reporting_data,
    payslip_upload,
    review_create,
    self_payslip_download,
)

from .conftest import FakeRequest

_PDF = b"%PDF-1.4\n%fake minimal pdf\n"


def _upload(content: bytes, name="bulletin.pdf") -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(content))


async def _employee(db, matricule="E1", **kw) -> Employee:
    emp = Employee(matricule=matricule, first_name="Alice", last_name="Martin", **kw)
    db.add(emp)
    await db.flush()
    return emp


async def test_payslip_upload_pdf_ok(db, staff_user):
    emp = await _employee(db)
    resp = await payslip_upload(
        emp.id, FakeRequest(), db, staff_user, file=_upload(_PDF), period="2026-06"
    )
    assert resp.status_code == 303
    p = (await db.execute(select(Payslip))).scalar_one()
    assert p.period == "2026-06"
    assert p.file_size == len(_PDF)
    assert p.content == _PDF


async def test_payslip_upload_rejects_non_pdf(db, staff_user):
    emp = await _employee(db)
    with pytest.raises(HTTPException) as exc:
        await payslip_upload(
            emp.id, FakeRequest(), db, staff_user,
            file=_upload(b"PK\x03\x04 zipdata", name="x.zip"), period="2026-06",
        )
    assert exc.value.status_code == 400


async def test_payslip_upload_invalid_period(db, staff_user):
    emp = await _employee(db)
    with pytest.raises(HTTPException) as exc:
        await payslip_upload(
            emp.id, FakeRequest(), db, staff_user, file=_upload(_PDF), period="juin"
        )
    assert exc.value.status_code == 400


async def test_self_payslip_download_scoping(db, staff_user):
    mine = await _employee(db, "MINE", user_id=1)
    other = await _employee(db, "OTHER")
    db.add(Payslip(employee_id=other.id, period="2026-06", filename="b.pdf",
                   content=_PDF, file_size=len(_PDF)))
    await db.flush()
    foreign = (await db.execute(select(Payslip))).scalar_one()
    # On ne télécharge pas le bulletin d'autrui.
    with pytest.raises(HTTPException) as exc:
        await self_payslip_download(foreign.id, FakeRequest(), db, staff_user)
    assert exc.value.status_code == 404
    assert mine.id  # (sanity) la fiche liée existe


async def test_review_create_validation(db, staff_user):
    emp = await _employee(db)
    f = {"review_type": "annuel", "review_date": "2026-03-01", "next_due_date": "2027-03-01"}
    resp = await review_create(emp.id, FakeRequest(f), db, staff_user)
    assert resp.status_code == 303
    r = (await db.execute(select(HrReview))).scalar_one()
    assert r.review_type == "annuel"

    with pytest.raises(HTTPException) as exc:
        await review_create(emp.id, FakeRequest({"review_type": "inconnu",
                                                 "review_date": "2026-03-01"}), db, staff_user)
    assert exc.value.status_code == 400


async def test_reporting_aggregates(db, staff_user):
    await _employee(db, "A", department="Commercial", status="active",
                    birth_date=date(1990, 1, 1), entry_date=date(2020, 1, 1))
    await _employee(db, "B", department="Commercial", status="active",
                    birth_date=date(1965, 1, 1), entry_date=date(2010, 1, 1))
    await _employee(db, "C", department="Ops", status="left",
                    exit_date=date(date.today().year, 2, 1))

    data = await _reporting_data(db)
    assert data["headcount"] == 2
    assert data["by_department"]["Commercial"] == 2
    assert data["exits_year"] == 1
    assert data["by_bracket"]["55+"] == 1
