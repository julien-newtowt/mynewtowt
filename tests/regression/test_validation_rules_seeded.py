"""Sentinelles — peuplement du référentiel de règles de validation.

Encode la leçon d'un incident de production réel (2026-09-04) : **tout import
QHSE échouait en 500**, parce que ``validation_rules`` ne contenait pas
``RQ01``-``RQ03``. Ces règles avaient été ajoutées à ``RULE_SEED`` le
2026-07-22, soit après le passage en production de la migration ``0097`` qui
sème le catalogue — et sans migration de rattrapage.

Le point vicieux : **aucun test ne pouvait le voir**. En dev et en test,
``seed_reference_data`` peuple tout au boot ; et une base reconstruite depuis
la chaîne complète de migrations est correcte elle aussi, puisque ``0097``
relit le ``RULE_SEED`` *courant* au moment où elle s'exécute. Seule une base
migrée avant l'ajout des règles est incomplète — il n'en existait qu'une, la
production.

D'où les deux sentinelles ci-dessous, qui visent la cause plutôt que le
symptôme : l'instantané figé doit rester aligné sur le catalogue, et une
migration ne doit plus jamais tirer son contenu du code applicatif vivant.
"""

from __future__ import annotations

import ast
import pathlib

from app.services.validation_engine import RULE_SEED

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"

# Constantes de peuplement : leur valeur évolue avec le code, donc une migration
# qui les importe n'a pas d'effet déterministe.
SEED_CONSTANTS = {"RULE_SEED", "THRESHOLD_SEED", "DASHBOARD_SEED"}

# Migrations antérieures à la règle, conservées telles quelles : réécrire une
# migration déjà passée en production serait bien pire que le défaut qu'elle
# porte. Cette liste ne doit jamais s'allonger.
GRANDFATHERED = {"20260709_0097_mrv_validation_socle.py"}


def test_frozen_qhse_rules_match_the_live_catalogue() -> None:
    """L'instantané figé de la migration 0142 == les règles QHSE de ``RULE_SEED``.

    La migration écrit ses valeurs en dur (c'est le correctif). Ce test empêche
    l'instantané et le catalogue de diverger en silence : si une règle QHSE
    change de sévérité ou de libellé dans le code, la divergence est signalée
    ici, et le rattrapage à faire (nouvelle migration) devient explicite.
    """
    module = MIGRATIONS_DIR / "20260904_0142_qhse_validation_rules_seed.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))

    frozen: dict[str, tuple] = {}
    for node in tree.body:
        # L'assignation est annotée (``QHSE_RULES: tuple[...] = (...)``) donc
        # ``AnnAssign`` ; on accepte les deux formes pour ne pas rendre la
        # sentinelle sensible à un simple retrait d'annotation.
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        else:
            continue
        if value is None or not any(getattr(t, "id", None) == "QHSE_RULES" for t in targets):
            continue
        for entry in ast.literal_eval(value):
            frozen[entry["rule_id"]] = (
                entry["domain"],
                entry["description"],
                entry["default_severity"],
                entry["scope"],
                entry["active"],
            )

    assert frozen, "QHSE_RULES introuvable dans la migration 0142"

    catalogue = {
        rid: (domain, desc, severity, scope, active)
        for (rid, domain, desc, severity, scope, active) in RULE_SEED
        if scope == "qhse"
    }
    assert frozen == catalogue


# Empreinte du catalogue au 2026-09-04 : 35 règles MRV (semées en production
# par 0097) + 3 règles QHSE (rattrapées par 0142).
PINNED_RULE_IDS: tuple[str, ...] = (
    "IR01",
    "IR02",
    "IR03",
    "IR04",
    "IR05",
    "R01",
    "R02",
    "R03",
    "R04",
    "R05",
    "R06",
    "R07",
    "R08",
    "R09",
    "R10",
    "R11",
    "R12",
    "R13",
    "R14",
    "R15",
    "R16",
    "R17",
    "R18",
    "R19",
    "R20",
    "R21",
    "R22",
    "R23",
    "R24",
    "R25",
    "R26",
    "R27",
    "R28",
    "R29",
    "R30",
    "RQ01",
    "RQ02",
    "RQ03",
)


def test_rule_catalogue_is_pinned_so_additions_cannot_skip_a_migration() -> None:
    """Tripwire : toute évolution du catalogue doit s'accompagner d'une migration.

    Le seed au boot ne couvre que le **développement**. Une règle ajoutée à
    ``RULE_SEED`` sans migration additive n'atteint jamais la production : elle
    y reste absente, et le premier résultat de contrôle qui la référence
    échoue sur la FK ``quality_check_results.rule_id`` — en emportant toute la
    transaction appelante. C'est très exactement l'incident DFT-20260904-001.

    Si ce test échoue, ce n'est pas lui qu'il faut corriger en premier :
    1. écrire une migration additive **idempotente** semant les règles
       ajoutées, valeurs **en dur** (cf. ``20260904_0142``) ;
    2. puis seulement mettre à jour l'empreinte ci-dessous.
    """
    assert tuple(sorted(rid for (rid, *_rest) in RULE_SEED)) == PINNED_RULE_IDS


def test_no_migration_imports_a_mutable_seed_constant() -> None:
    """Une migration est un instantané, pas un appel au code vivant.

    ``0097`` importe ``RULE_SEED`` : son effet dépend donc de la date à laquelle
    elle s'exécute. Une base construite aujourd'hui reçoit 38 règles, la
    production migrée en juillet en a reçu 35 — et la différence est
    structurellement invisible aux tests. C'est exactement le défaut qui a mis
    l'import QHSE à terre.
    """
    offenders: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        if path.name in GRANDFATHERED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app."):
                imported = {alias.name for alias in node.names}
                if imported & SEED_CONSTANTS:
                    offenders.append(f"{path.name} importe {sorted(imported & SEED_CONSTANTS)}")

    assert not offenders, (
        "Ces migrations tirent leur contenu du code applicatif — leur effet "
        "dépendrait de la date d'exécution : " + " ; ".join(offenders)
    )
