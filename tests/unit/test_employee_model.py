"""Tests des propriétés du modèle Employee (SIRH L1)."""

from __future__ import annotations

from datetime import date

from app.models.employee import Employee
from app.permissions import can_delete, can_edit, can_view


def test_full_name_and_active() -> None:
    e = Employee(matricule="E1", first_name="Alice", last_name="Martin", status="active")
    assert e.full_name == "Alice Martin"
    assert e.is_active is True
    e.status = "left"
    assert e.is_active is False


def test_seniority_none_when_no_entry_date() -> None:
    e = Employee(matricule="E1", first_name="A", last_name="B")
    assert e.seniority_years is None


def test_seniority_uses_exit_date_when_present() -> None:
    e = Employee(
        matricule="E1",
        first_name="A",
        last_name="B",
        entry_date=date(2020, 1, 1),
        exit_date=date(2023, 1, 1),
    )
    assert e.seniority_years == 3.0


def test_rh_role_permissions() -> None:
    # Le rôle RH dédié peut tout faire sur son module.
    assert can_view("rh", "rh")
    assert can_edit("rh", "rh")
    assert can_delete("rh", "rh")
    # Mais pas d'écriture ailleurs (consultation seule sur crew).
    assert can_view("rh", "crew")
    assert not can_edit("rh", "crew")
    # Armement rétrogradé en consultation seule sur rh.
    assert can_view("armement", "rh")
    assert not can_edit("armement", "rh")
