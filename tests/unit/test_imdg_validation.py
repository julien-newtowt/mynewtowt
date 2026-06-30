"""Validation IMDG / n° ONU (wizard de réservation, Vague 1) + garde-fou COM-08.

Couvre les helpers purs introduits pour fiabiliser la saisie des marchandises
dangereuses (sélecteur de classe + n° ONU) et verrouille la grille
d'annulation affichée (Step 3 / CGV) contre toute divergence silencieuse.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.booking import cancellation_fee_rate
from app.services.imdg import IMDG_CODES, is_valid_imdg_code, is_valid_un_number


# ───────────────────────── classe IMDG ─────────────────────────
@pytest.mark.parametrize("code", ["3", "9", "6.1", "1.1", "8"])
def test_known_imdg_codes_valid(code: str) -> None:
    assert is_valid_imdg_code(code)
    assert code in IMDG_CODES


@pytest.mark.parametrize("bad", ["3x", "", "99", "classe 3", "0", None])
def test_unknown_imdg_codes_rejected(bad) -> None:
    assert not is_valid_imdg_code(bad)


# ───────────────────────── n° ONU ─────────────────────────
@pytest.mark.parametrize("ok", ["UN1203", "UN 1203", "un1203", "Un 0004"])
def test_valid_un_numbers(ok: str) -> None:
    assert is_valid_un_number(ok)


@pytest.mark.parametrize("bad", ["1203", "UN12", "UN12034", "UNABCD", "", None, "1203UN"])
def test_invalid_un_numbers(bad) -> None:
    assert not is_valid_un_number(bad)


# ───────────────────────── grille COM-08 (garde-fou) ─────────────────────────
@pytest.mark.parametrize(
    "days, rate",
    [
        (None, "0"),  # ETD inconnu → pas de frais
        (60, "0"),  # > 30 j
        (31, "0"),
        (15, "0.25"),  # 30 → 7 j
        (3, "0.50"),  # 7 → 2 j
        (1, "1.00"),  # < 2 j
        (0, "1.00"),
    ],
)
def test_cancellation_grid_matches_displayed_tiers(days, rate) -> None:
    """La grille rendue (Step 3 + /about/terms : 0/25/50/100 %) doit refléter
    exactement ``cancellation_fee_rate`` — ce test casse si l'une diverge."""
    assert cancellation_fee_rate(days) == Decimal(rate)
