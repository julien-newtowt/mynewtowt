"""Tests de la classification commerciale des traversées (Europe ⇄ hors Europe)."""

from __future__ import annotations

from app.services.geo import is_european, leg_trade_category


def test_is_european() -> None:
    assert is_european("FR")
    assert is_european("pt")  # insensible à la casse
    assert not is_european("BR")
    assert not is_european("US")
    assert not is_european(None)


def test_export_europe_to_outside() -> None:
    assert leg_trade_category("FR", "BR") == "export"  # Fécamp → São Sebastião


def test_import_outside_to_europe() -> None:
    assert leg_trade_category("BR", "FR") == "import"


def test_hors_europe_both_outside() -> None:
    assert leg_trade_category("BR", "US") == "hors_europe"
    assert leg_trade_category("US", "BR") == "hors_europe"


def test_intra_europe_both_inside() -> None:
    assert leg_trade_category("FR", "PT") == "intra_eu"
    assert leg_trade_category("FR", "GB") == "intra_eu"


def test_unknown_country_treated_as_outside() -> None:
    # Pays inconnu / None → considéré hors Europe.
    assert leg_trade_category("FR", None) == "export"
    assert leg_trade_category(None, "FR") == "import"
