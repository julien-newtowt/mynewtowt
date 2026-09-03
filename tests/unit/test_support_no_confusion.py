"""Différenciation stricte entre le module `support` et le module `tickets`.

Deux modules, deux sujets sans rapport :

- ``tickets`` — incidents d'EXPLOITATION PORTUAIRE pendant une escale (avarie,
  avitaillement urgent, formalité douanière, urgence médicale…).
- ``support`` — difficultés rencontrées DANS LE LOGICIEL.

Ils n'ont ni le même public, ni les mêmes priorités, ni les mêmes droits. Ces
tests figent les règles de nomenclature de ``SPEC_SUPPORT_TICKETING.md`` §1 pour
que la confusion reste impossible, y compris dans six mois.
"""

from __future__ import annotations

import ast
import inspect

from app.models import support as support_models
from app.services import support as support_service


def test_no_class_is_named_ticket_without_the_support_prefix() -> None:
    """Règle 1 — le préfixe ``Support`` est obligatoire sur les classes."""
    classes = [
        name
        for name, obj in vars(support_models).items()
        if inspect.isclass(obj) and obj.__module__ == support_models.__name__
    ]
    assert classes, "aucune classe trouvée — test à vide"
    for name in classes:
        assert name.startswith("Support"), f"{name} doit être préfixé Support"


def test_table_names_are_namespaced() -> None:
    for cls in (
        support_models.SupportTicket,
        support_models.SupportTicketComment,
        support_models.SupportTicketAttachment,
    ):
        assert cls.__tablename__.startswith("support_"), cls.__tablename__
    # Et surtout : aucune collision avec les tables du module d'escale.
    assert support_models.SupportTicket.__tablename__ != "tickets"
    assert support_models.SupportTicketComment.__tablename__ != "ticket_comments"


def _imported_modules(module) -> set[str]:
    """Modules réellement importés, y compris les imports locaux de fonction.

    On parse l'AST plutôt que de chercher une sous-chaîne : les docstrings de ces
    deux modules **mentionnent volontairement** l'autre pour avertir de la
    confusion, et un test textuel les prendrait pour des imports. Première
    version de ce test : elle échouait sur ses propres avertissements.
    """
    tree = ast.parse(inspect.getsource(module))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_no_cross_import_between_the_two_services() -> None:
    """Règle 3 — aucun import croisé.

    Les deux modules ne partagent que les briques transverses (notifications,
    activity, safe_files). Un import croisé ferait dépendre l'un de l'autre et
    rendrait le vocabulaire poreux.
    """
    import app.services.tickets as tickets_service

    support_imports = _imported_modules(support_service)
    assert support_imports, "aucun import détecté — test à vide"
    assert not [m for m in support_imports if "services.tickets" in m]

    tickets_imports = _imported_modules(tickets_service)
    assert not [m for m in tickets_imports if "services.support" in m]


def test_router_does_not_import_the_other_router() -> None:
    from app.routers import support_router, tickets_router

    support_imports = _imported_modules(support_router)
    assert support_imports, "aucun import détecté — test à vide"
    assert not [m for m in support_imports if "tickets" in m]
    assert not [m for m in _imported_modules(tickets_router) if "support" in m]


def test_reference_prefixes_cannot_collide() -> None:
    """``SUP-`` d'un côté, ``TKT-`` de l'autre : aucun recouvrement possible."""
    from app.services.tickets import generate_reference as tkt_ref

    assert support_service.format_reference(2026, 1).startswith("SUP-")
    assert tkt_ref(2026).startswith("TKT-")


def test_url_roots_are_distinct() -> None:
    from app.routers import support_router, tickets_router

    assert support_router.router.prefix == "/support"
    assert tickets_router.router.prefix == "/tickets"


def test_permission_modules_are_distinct() -> None:
    from app.permissions import _MATRIX, MODULES, ROLES

    assert "support" in MODULES
    assert "tickets" in MODULES

    # Le besoin ne pouvait PAS être servi en étendant `tickets` : trois rôles
    # n'y ont aucun accès, et il faut que tout le monde puisse signaler un
    # problème logiciel.
    for role in ("armement", "commercial", "rh"):
        assert _MATRIX.get((role, "tickets")) is None, role
        assert _MATRIX.get((role, "support")) == "CM", role

    # Tous les rôles ont accès au support, sans exception.
    for role in ROLES:
        assert _MATRIX.get((role, "support")), role


def test_french_interface_labels_never_say_ticket() -> None:
    """Règle 2 — « ticket » ne doit pas apparaître dans les libellés du module.

    C'est ce qui empêche un utilisateur de chercher ses demandes dans
    « Tickets escale ». On dit « demande » / « Assistance ».
    """
    from app.i18n import fr

    keys = [k for k in fr.CATALOG if k.startswith("sup_") or k.startswith("s_nav_support")]
    assert len(keys) > 20, "trop peu de clés inspectées — test à vide"
    offenders = [k for k in keys if "ticket" in fr.CATALOG[k].lower()]
    assert not offenders, f"libellés parlant de « ticket » : {offenders}"


def test_support_service_exposes_no_plaintext_label() -> None:
    """Le service ne livre que des clés i18n — contrairement à ``tickets``.

    ``tickets`` porte 19 libellés français en dur (10 catégories + 3 priorités +
    6 statuts). Ce test empêche d'y revenir ici.
    """
    for name, obj in vars(support_service).items():
        if not name.isupper() or not isinstance(obj, dict):
            continue
        for value in obj.values():
            if isinstance(value, str):
                assert value.startswith("sup_"), f"{name} contient un libellé en clair : {value}"
