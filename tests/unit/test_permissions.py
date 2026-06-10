"""Permission matrix unit tests."""
from __future__ import annotations

import pytest

from app.permissions import (
    can_delete,
    can_edit,
    can_view,
    has_permission,
)


@pytest.mark.parametrize(
    "role,module,level,expected",
    [
        # Administrator full power
        ("administrateur", "booking", "S", True),
        ("administrateur", "finance", "S", True),
        # Commercial can view planning, modify commercial, but not delete planning
        ("commercial", "planning", "C", True),
        ("commercial", "planning", "M", False),
        ("commercial", "commercial", "S", True),
        # Marin can view crew but not modify
        ("marins", "crew", "C", True),
        ("marins", "crew", "M", False),
        # Operation cannot touch finance
        ("operation", "finance", "C", False),
        # Data analyst owns finance + analytics
        ("data_analyst", "finance", "S", True),
        ("data_analyst", "analytics", "S", True),
        # Manager has full control over tickets
        ("manager_maritime", "tickets", "S", True),
        # Unknown role / module deny
        ("unknown_role", "booking", "C", False),
        ("administrateur", "unknown_module", "C", False),
    ],
)
def test_has_permission(role: str, module: str, level: str, expected: bool) -> None:
    assert has_permission(role, module, level) is expected  # type: ignore[arg-type]


def test_legacy_role_mapping() -> None:
    # 'admin' should map to 'administrateur'
    assert has_permission("admin", "finance", "S") is True
    assert has_permission("manager", "escale", "M") is True


def test_helpers() -> None:
    assert can_view("operation", "planning")
    assert can_edit("operation", "planning")
    assert not can_delete("operation", "planning")
    assert can_delete("operation", "escale")
