"""Tests for app.services.mrv_export — SOF→MRV mapping.

Suppression totale du legacy MRV : les exports CSV DNV (retirés lot 10/14) et
``carbon_report_summary``/``CO2_EMISSION_FACTOR_MDO`` (retirés avec le reste du
legacy, plus aucun appelant) ont disparu. Ne subsiste que le mapping SOF→MRV,
consommé par ``services.voyage_events``.
"""

from __future__ import annotations

from app.services.mrv_export import map_sof_to_mrv_type


def test_sof_to_mrv_map_known_events():
    assert map_sof_to_mrv_type("SOSP") == "departure"
    assert map_sof_to_mrv_type("EOSP") == "arrival"
    assert map_sof_to_mrv_type("ANCHORED") == "begin_anchoring"


def test_sof_to_mrv_map_unknown_returns_none():
    assert map_sof_to_mrv_type("UNKNOWN_TYPE") is None


def test_to_dnv_csv_removed():
    """Le code mort ``to_dnv_csv`` (9 colonnes) a bien été purgé (lot 10)."""
    import app.services.mrv_export as me

    assert not hasattr(me, "to_dnv_csv")


def test_dnv_18_columns_export_removed_lot14():
    """LOT 14 (Q3) — l'export DNV 18 colonnes est RETIRÉ (OVDLA/OVDBR unique)."""
    import app.services.mrv_export as me

    assert not hasattr(me, "DNV_18_HEADERS")
    assert not hasattr(me, "dnv_csv_18")
    assert not hasattr(me, "build_dnv_rows")


def test_carbon_report_summary_removed():
    """Suppression totale du legacy MRV : plus d'appelant, symbole retiré."""
    import app.services.mrv_export as me

    assert not hasattr(me, "carbon_report_summary")
    assert not hasattr(me, "CO2_EMISSION_FACTOR_MDO")
