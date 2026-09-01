"""Garde-fou de saisie numérique — ``NaN``/``Infinity`` et bornes.

Constat de l'audit du 2026-08-27 : ``Decimal("nan")`` est un littéral **valide**
(pas d'``InvalidOperation``) et ``Decimal("nan") == 0`` vaut ``False``. Les
parseurs qui n'attrapaient que l'exception laissaient donc passer une valeur
non finie, que les gardes « montant non nul » en aval ne rattrapaient pas non
plus. Écrite dans ``cashbox_movements`` ou ``onboard_stock_movements`` — deux
tables sans route de suppression — elle rendait définitivement ``NaN`` le solde
de caisse ou l'inventaire du navire.

Ces tests verrouillent le comportement au niveau du parseur partagé ; les tests
de service (``test_onboard_sales.py``) verrouillent la reprise en profondeur.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.utils.decimals import CENTS, QTY_STEP, DecimalInputError, ensure_finite, parse_decimal


@pytest.mark.parametrize("raw", ["nan", "NaN", "-nan", "Infinity", "-Infinity", "inf"])
def test_non_finite_inputs_are_rejected(raw):
    """Le cœur du correctif : ces littéraux passaient tous avant."""
    with pytest.raises(DecimalInputError):
        parse_decimal(raw)


def test_nan_is_not_caught_by_a_zero_check():
    """Documente *pourquoi* la garde « != 0 » ne suffisait pas."""
    assert (Decimal("nan") == 0) is False
    with pytest.raises(DecimalInputError):
        ensure_finite(Decimal("nan"))


@pytest.mark.parametrize("raw", ["1e500", "99999999999", "-99999999999"])
def test_out_of_range_is_rejected(raw):
    with pytest.raises(DecimalInputError):
        parse_decimal(raw)


@pytest.mark.parametrize("raw", ["", "   ", "abc", "12,,5", None])
def test_unparseable_inputs_are_rejected(raw):
    with pytest.raises(DecimalInputError):
        parse_decimal(raw)


def test_french_input_is_accepted_and_quantized():
    assert parse_decimal("1 234,56", quantize=CENTS) == Decimal("1234.56")
    assert parse_decimal("12,5", quantize=CENTS) == Decimal("12.50")
    # Arrondi au pas de quantité de la colonne Numeric(12, 3).
    assert parse_decimal("2,0005", quantize=QTY_STEP) == Decimal("2.001")


def test_min_value_bound():
    assert parse_decimal("0", min_value=Decimal("0")) == Decimal("0")
    with pytest.raises(DecimalInputError):
        parse_decimal("-0.01", min_value=Decimal("0"))


def test_signed_values_pass_without_min_bound():
    """Un mouvement de stock est signé : la borne basse ne doit pas s'appliquer."""
    assert parse_decimal("-3", quantize=QTY_STEP) == Decimal("-3.000")
