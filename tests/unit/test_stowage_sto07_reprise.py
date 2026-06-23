"""STO-07 — capacité réelle par zone × format × gerbage (modèle de coefficients).

La V2 portait une matrice xlsx (zone × format × gerbé/simple). La V3 la restitue
par ``epal_footprint`` : capacité de zone en emplacements EPAL-équivalents,
pondérée par le format et le gerbage (palette gerbée = ½ empreinte plancher).
"""

from __future__ import annotations

from app.services.stowage import STACK_FOOTPRINT_FACTOR, epal_footprint


def test_epal_footprint_flat_epal():
    assert epal_footprint(10, "EPAL") == 10.0


def test_epal_footprint_format_coefficient():
    # BARRIQUE140 = 2.0 EPAL-équivalent ; USPAL = 1.2.
    assert epal_footprint(3, "BARRIQUE140") == 6.0
    assert abs(epal_footprint(5, "USPAL") - 6.0) < 1e-9


def test_epal_footprint_unknown_or_missing_format_defaults_to_one():
    assert epal_footprint(4, "ZZZ") == 4.0
    assert epal_footprint(4, None) == 4.0


def test_epal_footprint_none_count_is_zero():
    assert epal_footprint(None, "EPAL") == 0.0


def test_stacked_halves_footprint_when_zone_allows():
    assert (
        epal_footprint(10, "EPAL", is_stacked=True, stack_allowed=True)
        == 10.0 * STACK_FOOTPRINT_FACTOR
    )
    # Combine avec le coefficient de format : 3 × 2.0 × 0.5 = 3.0.
    assert epal_footprint(3, "BARRIQUE140", is_stacked=True, stack_allowed=True) == 3.0


def test_stacked_ignored_when_zone_forbids_stacking():
    # Gerbage déclaré mais zone non gerbable → empreinte pleine (pas de bonus).
    assert epal_footprint(10, "EPAL", is_stacked=True, stack_allowed=False) == 10.0
