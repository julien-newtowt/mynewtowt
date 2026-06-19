"""Role-based access control.

Source of truth for the RBAC matrix. `require_permission` is the
FastAPI dependency that protects router groups. Per-route reinforcement
is encouraged for M/S levels.

ARC-04 : la matrice codée en dur ``_MATRIX`` reste la valeur PAR DÉFAUT.
Des overrides par cellule (rôle × module) peuvent être posés en base
(table ``role_permissions``, écran /admin/permissions). Le chemin requête
(``require_permission``) consulte la matrice effective (défaut + overrides,
cache 60 s) ; toute erreur DB retombe — fail closed — sur ``_MATRIX``.
Les helpers synchrones (``has_permission``/``can_*``) restent sans DB et
ne voient que ``_MATRIX`` : ils servent à l'affichage (flags UI, chatbot),
pas au contrôle d'accès, qui est appliqué sur le chemin requête.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_staff
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
    "rh",
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
    "veille",
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
    # rh : consultation seule — l'écriture RH passe au rôle dédié ``rh``.
    ("armement", "rh"): "C",
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
    # rh — gestionnaire RH (SIRH sédentaires). Autorité de saisie/validation
    # sur le module rh ; consultation contextuelle ailleurs. La masse
    # salariale (finance) reste à arbitrer — défaut : pas d'accès.
    ("rh", "rh"): "CMS",
    ("rh", "planning"): "C",
    ("rh", "crew"): "C",
    ("rh", "finance"): "C",
    ("rh", "analytics"): "C",
    ("rh", "chat"): "CM",
    ("rh", "veille"): "C",
    # veille — informationnel (tout le staff consulte ; gestion des sources
    # pour les rôles transverses). administrateur a déjà CMS via la boucle.
    ("operation", "veille"): "CM",
    ("armement", "veille"): "C",
    ("technique", "veille"): "C",
    ("data_analyst", "veille"): "CM",
    ("marins", "veille"): "C",
    ("commercial", "veille"): "CM",
    ("manager_maritime", "veille"): "CMS",
}

_LEGACY_ROLE_MAP: dict[str, str] = {
    "admin": "administrateur",
    "manager": "operation",
    "operator": "operation",
    "viewer": "data_analyst",
}

_LEVEL_ORDER: dict[str, int] = {"C": 1, "M": 2, "S": 3}

# Valeurs autorisées pour une cellule de la grille (overrides inclus).
VALID_LEVELS: tuple[str, ...] = ("", "C", "CM", "CMS")


def _normalize_role(role: str) -> str:
    return _LEGACY_ROLE_MAP.get(role, role)


def _level_ok(granted: str, level: Level) -> bool:
    if not granted:
        return False
    required = _LEVEL_ORDER[level]
    return any(_LEVEL_ORDER.get(ch, 0) >= required for ch in granted)


# ─────────────────────────────────────────── Effective matrix (ARC-04)
# Cache module-level des overrides DB — TTL 60 s, invalidé par
# /admin/permissions. ``None`` = pas encore chargé (ou invalidé).
_PERMISSIONS_TTL_SECONDS = 60.0
_overrides_cache: dict[tuple[str, str], str] | None = None
_overrides_loaded_at: float = 0.0


def invalidate_permissions_cache() -> None:
    """Force la relecture des overrides au prochain check (post-update admin)."""
    global _overrides_cache, _overrides_loaded_at
    _overrides_cache = None
    _overrides_loaded_at = 0.0


async def _load_overrides(db: AsyncSession) -> dict[tuple[str, str], str]:
    """Charge les overrides ``role_permissions`` (cache 60 s).

    FAIL CLOSED : toute erreur DB (table absente, connexion HS…) renvoie
    ``{}`` → la matrice effective redevient exactement ``_MATRIX``. Le
    résultat (même vide sur erreur) est mis en cache pour ne pas marteler
    une DB en échec à chaque requête.
    """
    global _overrides_cache, _overrides_loaded_at
    now = time.monotonic()
    if _overrides_cache is not None and (now - _overrides_loaded_at) < _PERMISSIONS_TTL_SECONDS:
        return _overrides_cache

    overrides: dict[tuple[str, str], str] = {}
    try:
        from app.models.role_permission import RolePermission

        rows = (await db.execute(select(RolePermission))).scalars().all()
        for r in rows:
            if r.role in ROLES and r.module in MODULES and r.level in VALID_LEVELS:
                overrides[(r.role, r.module)] = r.level
    except Exception:
        overrides = {}

    _overrides_cache = overrides
    _overrides_loaded_at = now
    return overrides


async def get_effective_matrix(db: AsyncSession) -> dict[tuple[str, str], str]:
    """Matrice effective = ``_MATRIX`` + overrides DB (cache 60 s).

    Un override ``""`` retire l'accès ; un override non vide remplace le
    niveau par défaut. Garde-fou : la cellule (administrateur, admin) est
    toujours forcée à sa valeur par défaut — l'admin ne peut jamais se
    verrouiller hors de l'administration.
    """
    overrides = await _load_overrides(db)
    effective = dict(_MATRIX)
    for key, level in overrides.items():
        if level:
            effective[key] = level
        else:
            effective.pop(key, None)
    effective[("administrateur", "admin")] = _MATRIX[("administrateur", "admin")]
    return effective


def get_default_matrix() -> dict[tuple[str, str], str]:
    """Copie de la matrice codée en dur (référence pour l'écran admin)."""
    return dict(_MATRIX)


async def has_permission_effective(db: AsyncSession, role: str, module: str, level: Level) -> bool:
    """Check RBAC du chemin requête — matrice effective, fail closed."""
    try:
        matrix = await get_effective_matrix(db)
    except Exception:
        matrix = _MATRIX  # fail closed : jamais de crash auth
    return _level_ok(matrix.get((_normalize_role(role), module), ""), level)


def has_permission(role: str, module: str, level: Level) -> bool:
    """Check synchrone, sans DB — matrice par défaut UNIQUEMENT.

    N'inclut pas les overrides admin : utiliser pour l'affichage / les
    services sans session. Le contrôle d'accès effectif est appliqué par
    ``require_permission`` (matrice effective).
    """
    return _level_ok(_MATRIX.get((_normalize_role(role), module), ""), level)


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
        if not await has_permission_effective(db, user.role, module, level):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {module}/{level}",
            )
        # Pré-charge le compteur notif pour le topbar (read-only, ~1ms).
        try:
            from app.services.notifications import count_unread

            request.state.notif_count = await count_unread(
                db,
                user_id=user.id,
                user_role=user.role,
            )
        except Exception:
            request.state.notif_count = 0
        # État du Newtowt Agent (toggle /admin) pour masquer le widget topbar.
        try:
            from app.services.feature_flags import newtowt_agent_enabled

            request.state.newtowt_agent_enabled = await newtowt_agent_enabled(db)
        except Exception:
            request.state.newtowt_agent_enabled = True
        return user

    return _checker


def require_admin():
    """Shortcut for admin-only routes."""
    return require_permission("admin", "C")
