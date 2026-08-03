"""Les quatre vocabulaires de postes des classeurs Excel se rabattent sur l'enum.

Constat de `REFERENCE_METIER_RELEVES_EQUIPAGE.md` §3.5 : les classeurs de
l'Armement emploient **quatre** vocabulaires pour les mêmes fonctions —
anglais complet dans le bloc équipage (`Chief Engineer`), abréviations dans le
bloc « Crew Change » (`CHENG`, `BOSCO`, `CHOFF`), français dans la feuille de
manning (`Chef Mécanicien`), et codes suffixés dans la feuille `data`
(`CE*`, `CE Db`). MyTOWT en a un cinquième, son enum canonique **français**.

Sans table d'alias, un rapprochement laisse le rôle non résolu et l'armement
réglementaire (`vessel_readiness`) se croit incomplet.
"""

from __future__ import annotations

import pytest

from app.services.crew_compliance import (
    CANONICAL_ROLES,
    normalize_role,
    parse_role_token,
)


def test_canonical_list_matches_the_router_enum():
    """Garde-fou anti-dérive entre les deux déclarations de l'enum.

    `CANONICAL_ROLES` (service) duplique volontairement `CREW_ROLES` (routeur) :
    le routeur importe le service, l'inverse serait un import circulaire. Ce test
    interdit la dérive — c'est le prix assumé de la duplication.
    """
    from app.routers.crew_router import CREW_ROLES

    assert set(CANONICAL_ROLES) == set(CREW_ROLES)
    assert len(CANONICAL_ROLES) == len(CREW_ROLES)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        # Bloc équipage — anglais complet
        ("Master", "capitaine"),
        ("Chief Officer", "second"),
        ("Mate", "lieutenant"),
        ("Chief Engineer", "chef_mecanicien"),
        ("BOSUN", "bosco"),
        ("AB1", "marin"),
        ("AB2", "marin"),
        ("Fitter", "ajusteur"),
        ("Fitter / Oiler", "ajusteur"),
        ("Cook", "cook"),
        ("Deck Cadet", "eleve_officier"),
        ("Electrotech", "electricien"),
        # Bloc « Crew Change » — abréviations
        ("CHENG", "chef_mecanicien"),
        ("CHOFF", "second"),
        ("BOSCO", "bosco"),
        ("AB 1", "marin"),
        ("AB 2", "marin"),
        ("CADET", "eleve_officier"),
        ("ELECTRICAL ENGINEERING OFFICER ASSISTANT", "electricien"),
        # Feuille de manning — français
        ("Capitaine", "capitaine"),
        ("Second Capitaine", "second"),
        ("Lieutenant Pont", "lieutenant"),
        ("Chef Mécanicien", "chef_mecanicien"),
        ("Matelot", "marin"),
        ("Cuisinier", "cook"),
        # Feuille `data` — codes suffixés
        ("MASTER*", "capitaine"),
        ("CE*", "chef_mecanicien"),
        ("CO*", "second"),
        ("MATE*", "lieutenant"),
        ("BOSUN*", "bosco"),
        ("FITTER*", "ajusteur"),
        ("AB*", "marin"),
        ("COOK*", "cook"),
        ("ELECT", "electricien"),
        ("MASTER Db", "capitaine"),
        ("CE Db", "chef_mecanicien"),
    ],
)
def test_every_excel_label_resolves_to_a_canonical_role(label, expected):
    token = parse_role_token(label)
    assert token.role == expected, f"{label!r} non résolu"
    # Un alias ne doit jamais pointer hors de l'enum.
    assert token.role in CANONICAL_ROLES


@pytest.mark.parametrize(
    ("label", "mandatory", "understudy"),
    [
        ("MASTER*", True, False),
        ("CE*", True, False),
        ("MASTER Db", False, True),
        ("CE Db", False, True),
        ("Master", False, False),
        ("CADET", False, False),
    ],
)
def test_markers_are_read_separately(label, mandatory, understudy):
    """`*` (poste obligatoire) et `Db` (doublure) sont modélisés séparément.

    Yasmin les a groupés sous « obligatoire » (2026-08-03), mais rien dans les
    classeurs ne prouve qu'ils sont équivalents. Deux booléens distincts coûtent
    moins cher qu'une confusion figée : si la nuance se révèle réelle, elle est
    déjà portée par le modèle.
    """
    token = parse_role_token(label)
    assert token.is_mandatory is mandatory
    assert token.requires_understudy is understudy


def test_unresolved_label_is_reported_not_swallowed():
    """Le code neuf doit pouvoir SAVOIR qu'un libellé n'a pas été résolu."""
    token = parse_role_token("Chef de quart plongée")
    assert token.role is None
    assert token.raw == "Chef de quart plongée"
    assert token.cleaned == "chef de quart plongée"


def test_normalize_role_keeps_its_legacy_fallback():
    """⚠️ Non-régression : un rôle inconnu reste renvoyé, il ne DISPARAÎT pas.

    `vessel_readiness` construit la liste des postes présents à partir de
    `normalize_role`. Si celui-ci renvoyait `None` sur un libellé inconnu, le
    marin concerné **disparaîtrait de l'écran d'armement** — remplacer une donnée
    douteuse par une absence silencieuse serait le défaut miroir de celui qu'on
    corrige. Le code neuf utilise `parse_role_token` pour la distinction.
    """
    assert normalize_role("Poste Inconnu") == "poste inconnu"
    assert normalize_role("capitaine") == "capitaine"
    assert normalize_role(None) is None
    assert normalize_role("") is None
    assert normalize_role("   ") is None


def test_case_and_separators_do_not_matter():
    """Les classeurs écrivent « towt »/« TOWT », « AB 1 »/« AB1 », etc."""
    for variant in ("deck cadet", "DECK CADET", "Deck_Cadet", "  Deck Cadet  "):
        assert parse_role_token(variant).role == "eleve_officier"


def test_every_alias_target_is_canonical():
    """Aucun alias ne doit introduire un cinquième vocabulaire."""
    from app.services.crew_compliance import ROLE_SYNONYMS

    unknown = {v for v in ROLE_SYNONYMS.values() if v not in CANONICAL_ROLES}
    assert not unknown, f"alias pointant hors de l'enum : {sorted(unknown)}"


def test_required_roles_are_all_canonical():
    from app.services.crew_compliance import REQUIRED_ROLES

    assert set(REQUIRED_ROLES) <= set(CANONICAL_ROLES)
