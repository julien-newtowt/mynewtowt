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
        ("draft", "confirmed"),         # must go through submitted
        ("draft", "loaded"),
        ("submitted", "delivered"),
        ("delivered", "submitted"),
        ("cancelled", "submitted"),
        ("at_sea", "loaded"),           # cannot rewind
    ],
)
def test_invalid_transitions(current: str, target: str) -> None:
    with pytest.raises(InvalidStatusTransition):
        _assert_transition(current, target)


def test_reference_format() -> None:
    from app.services.booking import generate_reference

    ref = generate_reference(year=2026)
    assert ref.startswith("BK-2026-")
    assert len(ref) == 12  # BK-2026-XXXX (XXXX = 4 hex chars)
