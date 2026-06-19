"""Tests des validations de formulaire RH (400 plutôt que 500).

Couvre les correctifs post-revue : les conversions malformées (date,
nombre, entier) doivent lever HTTP 400, pas une exception non gérée (500).
Les parseurs prennent un objet « form » qui supporte ``.get()`` — un dict
suffit pour les tester sans monter une requête.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers.rh_router import (
    _absence_fields,
    _contract_from_form,
    _employee_from_form,
)


# ── Employé ─────────────────────────────────────────────────────────────
def test_employee_valid_minimal() -> None:
    data = _employee_from_form({"matricule": "E1", "first_name": "A", "last_name": "B"})
    assert data["matricule"] == "E1"
    assert data["status"] == "active"


def test_employee_bad_date_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _employee_from_form(
            {"matricule": "E1", "first_name": "A", "last_name": "B", "birth_date": "31/02/2020"}
        )
    assert exc.value.status_code == 400


def test_employee_bad_status_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _employee_from_form(
            {"matricule": "E1", "first_name": "A", "last_name": "B", "status": "zombie"}
        )
    assert exc.value.status_code == 400


# ── Contrat ─────────────────────────────────────────────────────────────
def test_contract_fixed_term_requires_end_date() -> None:
    with pytest.raises(HTTPException) as exc:
        _contract_from_form({"contract_type": "cdd", "start_date": "2026-01-01"})
    assert exc.value.status_code == 400


def test_contract_end_before_start_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _contract_from_form(
            {"contract_type": "cdi", "start_date": "2026-06-01", "end_date": "2026-01-01"}
        )
    assert exc.value.status_code == 400


def test_contract_parent_marks_amendment() -> None:
    data = _contract_from_form(
        {"contract_type": "cdi", "start_date": "2026-01-01", "parent_contract_id": "7"}
    )
    assert data["is_amendment"] is True
    assert data["parent_contract_id"] == 7


def test_contract_bad_parent_id_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _contract_from_form(
            {"contract_type": "cdi", "start_date": "2026-01-01", "parent_contract_id": "x"}
        )
    assert exc.value.status_code == 400


# ── Absence ─────────────────────────────────────────────────────────────
def test_absence_valid_computes_business_days() -> None:
    data = _absence_fields(
        {"kind": "cp", "start_date": "2026-06-15", "end_date": "2026-06-19"}
    )
    assert data["business_days"] == Decimal("5")
    assert data["start_date"] == date(2026, 6, 15)


def test_absence_bad_kind_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _absence_fields({"kind": "vacances", "start_date": "2026-06-15", "end_date": "2026-06-19"})
    assert exc.value.status_code == 400


def test_absence_bad_date_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _absence_fields({"kind": "cp", "start_date": "nope", "end_date": "2026-06-19"})
    assert exc.value.status_code == 400


def test_absence_end_before_start_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _absence_fields({"kind": "cp", "start_date": "2026-06-19", "end_date": "2026-06-15"})
    assert exc.value.status_code == 400
