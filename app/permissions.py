"""Role-based access control.

Source of truth for the RBAC matrix. `require_permission` is the
FastAPI dependency that protects router groups. Per-route reinforcement
is encouraged for M/S levels.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthRequired, get_current_staff
from app.database import get_db

Level = Literal["C", "M", "S"]  # Consult / Modify / Suppress

# Roles available in the system (keep stable; legacy aliases mapped below).
ROLES: tuple[str, ...] = (
    "administrateur",
    "operation",
    "armement",
    "technique",
    "data_analyst",
    "marins",
    "commercial",
    "manager_maritime",
)

MODULES: tuple[str, ...] = (
    "planning",
    "commercial",
    "escale",
    "cargo",
    "finance",
    "kpi",
    "captain",
    "crew",
    "claims",
    "mrv",
    "rh",
    "booking",
    "tickets",
    "analytics",
    "chat",
    "admin",
)

# RBAC matrix — keys: (role, module), value: highest level granted.
_MATRIX: dict[tuple[str, str], str] = {
    # administrateur — full control
    **{("administrateur", m): "CMS" for m in MODULES},
    # operation
    ("operation", "planning"): "CM",
    ("operation", "commercial"): "CM",
    ("operation", "escale"): "CMS",
    ("operation", "cargo"): "CMS",
    ("operation", "kpi"): "C",
    ("operation", "captain"): "CM",
    ("operation", "crew"): "CM",
    ("operation", "claims"): "CMS",
    ("operation", "mrv"): "CM",
    ("operation", "rh"): "C",
    ("operation", "booking"): "CM",
    ("operation", "tickets"): "CMS",
    ("operation", "analytics"): "C",
    ("operation", "chat"): "CM",
    # armement
    ("armement", "planning"): "C",
    ("armement", "escale"): "C",
    ("armement", "kpi"): "C",
    ("armement", "captain"): "C",
    ("armement", "crew"): "CMS",
    ("armement", "mrv"): "C",
    ("armement", "rh"): "CM",
    ("armement", "chat"): "C",
    # technique
    ("technique", "planning"): "C",
    ("technique", "commercial"): "C",
    ("technique", "escale"): "CMS",
    ("technique", "cargo"): "C",
    ("technique", "kpi"): "C",
    ("technique", "captain"): "CM",
    ("technique", "crew"): "C",
    ("technique", "claims"): "C",
    ("technique", "mrv"): "CM",
    ("technique", "rh"): "C",
    ("technique", "tickets"): "CM",
    ("technique", "chat"): "C",
    # data_analyst
    ("data_analyst", "planning"): "C",
    ("data_analyst", "commercial"): "C",
    ("data_analyst", "escale"): "C",
    ("data_analyst", "cargo"): "C",
    ("data_analyst", "finance"): "CMS",
    ("data_analyst", "kpi"): "C",
    ("data_analyst", "captain"): "C",
    ("data_analyst", "crew"): "C",
    ("data_analyst", "claims"): "C",
    ("data_analyst", "mrv"): "CM",
    ("data_analyst", "rh"): "C",
    ("data_analyst", "booking"): "C",
    ("data_analyst", "tickets"): "C",
    ("data_analyst", "analytics"): "CMS",
    ("data_analyst", "chat"): "C",
    # marins
    ("marins", "planning"): "C",
    ("marins", "escale"): "C",
    ("marins", "kpi"): "C",
    ("marins", "captain"): "C",
    ("marins", "crew"): "C",
    ("marins", "cargo"): "C",
    ("marins", "mrv"): "C",
    ("marins", "rh"): "C",
    ("marins", "tickets"): "CM",
    ("marins", "chat"): "C",
    # commercial
    ("commercial", "planning"): "C",
    ("commercial", "commercial"): "CMS",
    ("commercial", "cargo"): "CM",
    ("commercial", "escale"): "C",
    ("commercial", "kpi"): "C",
    ("commercial", "captain"): "C",
    ("commercial", "rh"): "C",
    ("commercial", "booking"): "CMS",
    ("commercial", "analytics"): "C",
    ("commercial", "chat"): "C",
    # manager_maritime
    ("manager_maritime", "planning"): "CM",
    ("manager_maritime", "commercial"): "CM",
    ("manager_maritime", "escale"): "CM",
    ("manager_maritime", "cargo"): "CM",
    ("manager_maritime", "kpi"): "C",
    ("manager_maritime", "captain"): "CMS",
    ("manager_maritime", "crew"): "CM",
    ("manager_maritime", "claims"): "CM",
    ("manager_maritime", "mrv"): "CM",
    ("manager_maritime", "rh"): "C",
    ("manager_maritime", "booking"): "CM",
    ("manager_maritime", "tickets"): "CMS",
    ("manager_maritime", "analytics"): "CM",
    ("manager_maritime", "chat"): "CM",
    ("manager_maritime", "admin"): "C",
}

_LEGACY_ROLE_MAP: dict[str, str] = {
    "admin": "administrateur",
    "manager": "operation",
    "operator": "operation",
    "viewer": "data_analyst",
    "gestionnaire_passagers": "commercial",  # deprecated
}

_LEVEL_ORDER: dict[str, int] = {"C": 1, "M": 2, "S": 3}


def _normalize_role(role: str) -> str:
    return _LEGACY_ROLE_MAP.get(role, role)


def has_permission(role: str, module: str, level: Level) -> bool:
    granted = _MATRIX.get((_normalize_role(role), module), "")
    if not granted:
        return False
    required = _LEVEL_ORDER[level]
    return any(_LEVEL_ORDER[ch] >= required for ch in granted)


def can_view(role: str, module: str) -> bool:
    return has_permission(role, module, "C")


def can_edit(role: str, module: str) -> bool:
    return has_permission(role, module, "M")


def can_delete(role: str, module: str) -> bool:
    return has_permission(role, module, "S")


def has_any_access(role: str, module: str) -> bool:
    return can_view(role, module)


def require_permission(module: str, level: Level):
    """FastAPI dependency factory.

    En plus du check RBAC, attache ``request.state.notif_count`` (compteur
    de notifications non lues pour ce user/rôle) — exploité par le context
    processor Jinja ``_staff_layout_context`` pour alimenter le badge cloche
    du topbar sur toutes les pages staff.
    """

    async def _checker(
        request: Request,
        user=Depends(get_current_staff),
        db: AsyncSession = Depends(get_db),
    ):
        if not has_permission(user.role, module, level):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {module}/{level}",
            )
        # Pré-charge le compteur notif pour le topbar (read-only, ~1ms).
        try:
            from app.services.notifications import count_unread
            request.state.notif_count = await count_unread(
                db, user_id=user.id, user_role=user.role,
            )
        except Exception:
            request.state.notif_count = 0
        return user

    return _checker


def require_admin():
    """Shortcut for admin-only routes."""
    return require_permission("admin", "C")
