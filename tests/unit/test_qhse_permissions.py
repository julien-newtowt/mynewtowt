"""Tests — matrice RBAC du module ``qhse`` (Phase 0).

Vérifie le mapping validé (cahier des charges §7 / wireframes) : accès
dédié pour ``manager_maritime``/``technique``/``marins``, transverse lecture
seule pour les 5 autres rôles métier, et accès complet automatique pour
``administrateur``. Purement synchrone — ``has_permission`` ne consulte que
la matrice par défaut, sans DB (cf. ``app.permissions``).
"""

from __future__ import annotations

from app.permissions import MODULES, ROLES, has_permission


def test_qhse_is_a_registered_module():
    assert "qhse" in MODULES


def test_manager_maritime_has_full_access():
    assert has_permission("manager_maritime", "qhse", "C")
    assert has_permission("manager_maritime", "qhse", "M")
    assert has_permission("manager_maritime", "qhse", "S")


def test_technique_can_view_and_edit_but_not_delete():
    assert has_permission("technique", "qhse", "C")
    assert has_permission("technique", "qhse", "M")
    assert not has_permission("technique", "qhse", "S")


def test_marins_can_view_and_edit_but_not_delete():
    assert has_permission("marins", "qhse", "C")
    assert has_permission("marins", "qhse", "M")
    assert not has_permission("marins", "qhse", "S")


def test_administrateur_has_full_access_like_every_other_module():
    assert has_permission("administrateur", "qhse", "C")
    assert has_permission("administrateur", "qhse", "M")
    assert has_permission("administrateur", "qhse", "S")


def test_transverse_roles_are_read_only():
    for role in ("data_analyst", "operation", "armement", "commercial", "rh"):
        assert has_permission(role, "qhse", "C"), f"{role} devrait avoir C sur qhse"
        assert not has_permission(role, "qhse", "M"), f"{role} ne devrait pas avoir M sur qhse"


def test_every_role_has_at_least_read_access():
    """Aucun des 9 rôles n'est totalement exclu du module QHSE (§7 : lecture
    seule au minimum pour visibilité fleet-wide, mémé pour les rôles sans
    workspace dédié)."""
    for role in ROLES:
        assert has_permission(role, "qhse", "C"), f"{role} devrait au moins avoir C sur qhse"
