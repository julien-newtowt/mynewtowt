"""Tests du générateur CSV d'export Silae (SIRH L5)."""

from __future__ import annotations

from decimal import Decimal

from app.services.silae_export import CSV_HEADER, build_evp_csv


def test_header_and_bom():
    out = build_evp_csv([])
    assert out.startswith("﻿")  # BOM UTF-8
    first_line = out.lstrip("﻿").splitlines()[0]
    assert first_line == ";".join(CSV_HEADER)


def test_row_serialisation_semicolon():
    rows = [
        {
            "matricule": "E1",
            "silae_id": "S1",
            "nom": "Martin",
            "prenom": "Alice",
            "periode": "2026-06",
            "type_evp": "heures_supp",
            "libelle": "Heures supplémentaires",
            "quantite": Decimal("3.5"),
            "montant": Decimal("120.00"),
            "commentaire": "nuit",
        }
    ]
    out = build_evp_csv(rows).lstrip("﻿")
    line = out.splitlines()[1]
    assert line == "E1;S1;Martin;Alice;2026-06;heures_supp;Heures supplémentaires;3.5;120.00;nuit"


def test_missing_keys_and_none_become_empty():
    rows = [{"matricule": "E2", "montant": None}]
    out = build_evp_csv(rows).lstrip("﻿")
    cells = out.splitlines()[1].split(";")
    assert cells[0] == "E2"
    # montant None → vide ; toutes les autres colonnes absentes → vides
    assert cells[8] == ""
    assert len(cells) == len(CSV_HEADER)
