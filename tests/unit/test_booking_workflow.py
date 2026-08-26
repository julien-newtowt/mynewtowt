"""Booking status transition tests — pure logic."""

from __future__ import annotations

import pytest

from app.services.booking import InvalidStatusTransition, _assert_transition  # type: ignore


@pytest.mark.parametrize(
    "current,target",
    [
        ("draft", "submitted"),
        ("draft", "cancelled"),
        ("submitted", "confirmed"),
        ("submitted", "cancelled"),
        ("confirmed", "loaded"),
        ("loaded", "at_sea"),
        ("at_sea", "discharged"),
        ("discharged", "delivered"),
    ],
)
def test_valid_transitions(current: str, target: str) -> None:
    _assert_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        ("draft", "confirmed"),  # must go through submitted
        ("draft", "loaded"),
        ("submitted", "delivered"),
        ("delivered", "submitted"),
        ("cancelled", "submitted"),
        ("at_sea", "loaded"),  # cannot rewind
    ],
)
def test_invalid_transitions(current: str, target: str) -> None:
    with pytest.raises(InvalidStatusTransition):
        _assert_transition(current, target)


def test_reference_format() -> None:
    """Format et **entropie** de la référence (M-6).

    Le suffixe faisait 4 caractères hexadécimaux (16 bits) : une collision
    devenait probable dès quelques centaines de réservations dans l'année, et
    se manifestait par un HTTP 500 à la validation du tunnel client. Le test
    verrouille désormais le plancher d'entropie, pas seulement le préfixe.
    """
    from app.services.booking import generate_reference

    ref = generate_reference(year=2026)
    assert ref.startswith("BK-2026-")
    suffix = ref.removeprefix("BK-2026-")
    assert len(suffix) >= 10  # ≥ 40 bits
    assert len(ref) <= 20  # tient dans Booking.reference (String(20))
    assert set(suffix) <= set("0123456789ABCDEF")
    # Deux tirages consécutifs ne doivent pas coïncider.
    assert generate_reference(year=2026) != ref
