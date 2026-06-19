"""Tests du parseur d'import collaborateurs (SIRH L1)."""

from __future__ import annotations

from datetime import date

from app.services.hr_import import parse_employees_csv


def test_parse_minimal_valid() -> None:
    csv = "matricule,prenom,nom\nE001,Alice,Martin\nE002,Bob,Durand\n"
    res = parse_employees_csv(csv)
    assert res.ok
    assert len(res.rows) == 2
    assert res.rows[0]["matricule"] == "E001"
    assert res.rows[0]["first_name"] == "Alice"
    assert res.rows[1]["last_name"] == "Durand"


def test_semicolon_delimiter_and_accented_headers() -> None:
    csv = "Matricule;Prénom;Nom;Service;Date d'entrée\nE10;Chloé;Roy;Commercial;2024-03-01\n"
    res = parse_employees_csv(csv)
    assert res.ok
    row = res.rows[0]
    assert row["department"] == "Commercial"
    assert row["entry_date"] == date(2024, 3, 1)


def test_french_dates() -> None:
    csv = "matricule;prenom;nom;date_entree\nE1;Jean;Bon;15/01/2023\n"
    res = parse_employees_csv(csv)
    assert res.ok, res.errors
    assert res.rows[0]["entry_date"] == date(2023, 1, 15)


def test_missing_required_column() -> None:
    csv = "matricule,prenom\nE1,Alice\n"
    res = parse_employees_csv(csv)
    assert not res.ok
    assert any("manquantes" in e.message for e in res.errors)
    assert res.rows == []


def test_invalid_status_and_date_reported_per_line() -> None:
    csv = "matricule,prenom,nom,statut,date_entree\nE1,A,B,bogus,2020-01-01\nE2,C,D,active,nope\n"
    res = parse_employees_csv(csv)
    assert len(res.rows) == 0
    assert len(res.errors) == 2
    assert res.errors[0].line == 2
    assert "statut invalide" in res.errors[0].message
    assert "date invalide" in res.errors[1].message


def test_duplicate_matricule_in_file() -> None:
    csv = "matricule,prenom,nom\nE1,A,B\nE1,C,D\n"
    res = parse_employees_csv(csv)
    assert len(res.rows) == 1
    assert any("double" in e.message for e in res.errors)


def test_blank_lines_skipped_and_bom_stripped() -> None:
    csv = "﻿matricule,prenom,nom\nE1,A,B\n\n   \nE2,C,D\n"
    res = parse_employees_csv(csv)
    assert res.ok
    assert len(res.rows) == 2


def test_empty_file() -> None:
    res = parse_employees_csv("")
    assert not res.ok
    assert res.rows == []
