"""Tickets service — workflow transitions, SLA, reference generation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.tickets import (
    PRIORITY_SLA_HOURS,
    InvalidTicketTransition,
    assert_transition,
    generate_reference,
    is_sla_breached,
    sla_target,
)


def test_reference_format() -> None:
    ref = generate_reference(year=2026)
    assert ref.startswith("TKT-2026-")
    assert len(ref) == 13  # TKT-2026-XXXX


def test_sla_target_p1_is_two_hours() -> None:
    now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    t = sla_target("P1", now)
    assert t == now + timedelta(hours=2)


def test_sla_target_p2_is_eight_hours() -> None:
    now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    assert sla_target("P2", now) == now + timedelta(hours=8)


def test_sla_target_p3_is_seventy_two_hours() -> None:
    now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    assert sla_target("P3", now) == now + timedelta(hours=72)


def test_sla_target_unknown_priority_defaults_to_72h() -> None:
    now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    assert sla_target("PX", now) == now + timedelta(hours=72)


@pytest.mark.parametrize("current,target", [
    ("open", "in_progress"),
    ("open", "cancelled"),
    ("in_progress", "pending_external"),
    ("in_progress", "resolved"),
    ("in_progress", "cancelled"),
    ("pending_external", "in_progress"),
    ("pending_external", "resolved"),
    ("resolved", "closed"),
    ("resolved", "in_progress"),
])
def test_valid_transitions(current: str, target: str) -> None:
    assert_transition(current, target)


@pytest.mark.parametrize("current,target", [
    ("open", "resolved"),
    ("open", "closed"),
    ("closed", "in_progress"),
    ("cancelled", "open"),
    ("resolved", "open"),
])
def test_invalid_transitions(current: str, target: str) -> None:
    with pytest.raises(InvalidTicketTransition):
        assert_transition(current, target)


def test_is_sla_breached_active_ticket() -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    ticket = SimpleNamespace(
        status="in_progress",
        sla_target_at=past,
        sla_breached=False,
    )
    assert is_sla_breached(ticket) is True


def test_is_sla_breached_resolved_uses_stored_flag() -> None:
    """Once resolved/closed, breach status is frozen (stored at resolve time)."""
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    ticket = SimpleNamespace(
        status="resolved",
        sla_target_at=past,
        sla_breached=False,  # was OK at resolve time
    )
    assert is_sla_breached(ticket) is False


def test_is_sla_breached_future_target() -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    ticket = SimpleNamespace(
        status="open",
        sla_target_at=future,
        sla_breached=False,
    )
    assert is_sla_breached(ticket) is False


def test_priority_sla_table_complete() -> None:
    assert PRIORITY_SLA_HOURS == {"P1": 2, "P2": 8, "P3": 72}
