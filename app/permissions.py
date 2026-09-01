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
    # Vente à bord + caisse de bord. Module **distinct** de `captain`
    # délibérément : le commandant doit pouvoir encaisser sans recevoir pour
    # autant le droit d'écrire les SOF, les décalages d'ETA, les documents
    # cargo et la saisie MRV — que `captain:M` déverrouille sur toute la flotte,
    # sans contrôle de navire. Donner tout le module pour une fonctionnalité
    # était une escalade de privilège (revue de sécurité du 2026-08-28).
    "ventes",
    "crew",
    "claims",
    "mrv",
    "qhse",
    "rh",
    "booking",
    "tickets",
    "analytics",
    "chat",
    "veille",
    "support",
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
    ("operation", "ventes"): "CM",
    ("operation", "crew"): "CM",
    ("operation", "claims"): "CMS",
    ("operation", "mrv"): "CM",
    ("operation", "qhse"): "C",
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
    ("armement", "ventes"): "C",
    ("armement", "crew"): "CMS",
    ("armement", "mrv"): "C",
    ("armement", "qhse"): "C",
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
    ("technique", "ventes"): "CM",
    ("technique", "crew"): "C",
    ("technique", "claims"): "C",
    ("technique", "mrv"): "CM",
    ("technique", "qhse"): "CM",
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
    ("data_analyst", "ventes"): "C",
    ("data_analyst", "crew"): "C",
    ("data_analyst", "claims"): "C",
    ("data_analyst", "mrv"): "CM",
    ("data_analyst", "qhse"): "C",
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
    # Le commandant encaisse : c'est sa fonction. Le cloisonnement par navire
    # (ADR-012) borne ensuite chaque route au navire d'affectation.
    ("marins", "ventes"): "CM",
    ("marins", "crew"): "C",
    ("marins", "cargo"): "C",
    ("marins", "mrv"): "C",
    ("marins", "qhse"): "CM",
    ("marins", "rh"): "C",
    ("marins", "tickets"): "CM",
    ("marins", "chat"): "C",
    # commercial
    ("commercial", "planning"): "C",
    ("commercial", "commercial"): "CMS",
    ("commercial", "cargo"): "CM",
    ("commercial", "escale"): "C",
    ("commercial", "kpi"): "C",
    ("commercial", "qhse"): "C",
    ("commercial", "captain"): "C",
    ("commercial", "ventes"): "C",
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
    ("manager_maritime", "ventes"): "CMS",
    ("manager_maritime", "crew"): "CM",
    ("manager_maritime", "claims"): "CM",
    ("manager_maritime", "mrv"): "CM",
    ("manager_maritime", "qhse"): "CMS",
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
    ("rh", "qhse"): "C",
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
    # support — Assistance : signaler une difficulté rencontrée DANS LE LOGICIEL.
    # ⚠️ Rien à voir avec `tickets`, qui porte les incidents d'exploitation
    # portuaire (cf. SPEC_SUPPORT_TICKETING §1).
    #
    # TOUS les rôles obtiennent CM — c'est précisément pourquoi ce besoin ne
    # pouvait pas être servi en étendant `tickets` : armement, commercial et rh
    # n'y ont aucun accès, et leur ouvrir le kanban d'escale aurait été absurde.
    # administrateur a déjà CMS via la boucle sur MODULES.
    #
    # ⚠️ La matrice ne sait PAS exprimer « voir les siennes » vs « voir toutes ».
    # Ce cloisonnement, et la réserve du tri à administrateur, vivent dans
    # `support_router` — pas ici. Le niveau S n'est PAS détourné pour signifier
    # « peut trier » : S veut dire Suppress.
    **{
        (r, "support"): "CM"
        for r in (
            "operation",
            "armement",
            "technique",
            "data_analyst",
            "marins",
            "commercial",
            "manager_maritime",
            "rh",
        )
    },
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


def is_administrator(role: str) -> bool:
    """``True`` si ``role`` est l'administrateur, rôles legacy normalisés.

    Certaines décisions ne sont pas exprimables dans la matrice (rôle × module ×
    niveau) : le module ``support`` doit distinguer « voir les siennes » de
    « voir toutes », et réserver le tri. Ces règles vivent dans le routeur et
    s'appuient sur ce helper.

    À utiliser plutôt qu'un ``role == "administrateur"`` en dur, qui raterait un
    compte encore porteur du rôle legacy ``admin``. Et plutôt qu'un
    ``has_permission(role, module, "S")`` détourné : ``S`` signifie *Suppress*,
    lui faire dire « peut trier » serait un mensonge sémantique.
    """
    return _normalize_role(role) == "administrateur"


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
        # Pré-charge le compteur notif + les 5 plus récentes pour le topbar
        # (read-only, ~1-2ms). UX-04 : la cloche affiche le vrai flux.
        try:
            from app.services.notifications import count_unread, list_for

            request.state.notif_count = await count_unread(
                db,
                user_id=user.id,
                user_role=user.role,
            )
            request.state.recent_notifications = await list_for(
                db,
                user_id=user.id,
                user_role=user.role,
                limit=5,
            )
        except Exception:
            request.state.notif_count = 0
            request.state.recent_notifications = []
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


# ── Cloisonnement par navire (ADR-012) ───────────────────────────────────────
#
# Décision du 2026-08-27 : le **personnel maritime est borné à son navire
# d'affectation**. Les seules consultations ouvertes sur la flotte entière sont
# le planning de navigation et la position des navires — donc pas la caisse, pas
# les ventes, pas l'inventaire, pas le registre.
#
# La règle s'appuie sur ``User.assigned_vessel_id`` plutôt que sur une liste de
# rôles : c'est le rattachement qui fait le marin, et un technicien embarqué doit
# être borné comme lui. Deux exceptions nommées, qui doivent pouvoir corriger et
# administrer à distance.

#: Rôles jamais bornés : ils administrent la flotte depuis le siège.
FLEET_WIDE_ROLES: frozenset[str] = frozenset({"administrateur", "armement"})

#: Rôles considérés comme personnel maritime : sans navire d'affectation, ils
#: n'ont accès à rien plutôt qu'à tout — un cloisonnement qui laisse passer les
#: comptes mal renseignés ne cloisonne rien.
SEAFARER_ROLES: frozenset[str] = frozenset({"marins"})


class VesselAccessDenied(Exception):
    """Accès à un navire hors périmètre. Message destiné à l'utilisateur."""


def assert_vessel_access(user, vessel_id: int | None) -> None:
    """Vérifie qu'un utilisateur a le droit d'agir sur ce navire.

    Lève ``VesselAccessDenied`` avec un message **actionnable** : c'est un 403
    muet, sans cause ni remède affichés, qui a fait échouer le premier test à
    bord du module de vente. Un refus doit dire quoi faire.
    """
    role = getattr(user, "role", None)
    if role in FLEET_WIDE_ROLES:
        return
    assigned = getattr(user, "assigned_vessel_id", None)
    if assigned is None:
        if role in SEAFARER_ROLES:
            raise VesselAccessDenied(
                "Votre compte n'est rattaché à aucun navire. Demandez au siège de "
                "le rattacher à votre navire d'affectation (écran /admin/users)."
            )
        # Rôle de terre sans affectation : périmètre flotte, comme aujourd'hui.
        return
    if vessel_id is not None and int(assigned) != int(vessel_id):
        raise VesselAccessDenied(
            "Vous n'avez accès qu'à votre navire d'affectation. "
            "Contactez le siège si votre rattachement doit changer."
        )


def visible_vessel_id(user) -> int | None:
    """Navire auquel l'utilisateur est borné, ou ``None`` s'il voit la flotte."""
    if getattr(user, "role", None) in FLEET_WIDE_ROLES:
        return None
    return getattr(user, "assigned_vessel_id", None)
