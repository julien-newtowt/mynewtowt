"""QHSE — sème les règles RQ01-RQ03 dans ``validation_rules`` (rattrapage prod).

Répare un défaut de production constaté le 2026-09-04 : **tout import QHSE
échouait en 500**, sans qu'aucune ligne ne soit écrite.

Chaîne du défaut
----------------
``quality_check_results.rule_id`` porte une FK vers
``validation_rules.rule_id``. Le socle de règles a été semé en production par
la migration ``20260709_0097``, qui **importe la constante ``RULE_SEED`` du
code applicatif** au moment de son exécution. Les trois règles QHSE
(``RQ01``-``RQ03``) ont été ajoutées à ``RULE_SEED`` le 2026-07-22 (fondations
QHSE Phase 0, commit ``1145d73``) — soit **après** le passage de ``0097`` en
production, et **sans migration**. La base de production contient donc les 35
règles MRV et aucune règle QHSE.

Tant que les règles QHSE étaient enregistrées mais jamais exécutées, l'absence
était inoffensive. Le lot de câblage RQ01-03 (PR #195) les exécute réellement :
l'``INSERT`` du résultat viole la FK, l'``IntegrityError`` remonte hors du
gestionnaire par ligne de ``import_qhse_xlsx`` et emporte la transaction
entière.

Pourquoi aucun test ne pouvait le voir
--------------------------------------
En dev et en test, ``seed_reference_data`` sème les 38 règles au boot
(``app/main.py``, conditionné à ``app_env == "development"``). Et une base
reconstruite aujourd'hui depuis la chaîne complète de migrations les a aussi,
puisque ``0097`` relit le ``RULE_SEED`` **courant**. Seule une base migrée
*avant* le 2026-07-22 est incomplète : il n'en existe qu'une, la production.

C'est la leçon de fond : **une migration qui importe une constante du code
applicatif n'est pas déterministe** — son effet dépend de la date à laquelle
elle s'exécute, et le défaut est structurellement invisible aux tests. Les
valeurs ci-dessous sont donc écrites **en dur** : une migration est un
instantané, pas un appel au code vivant. Elles doivent rester alignées avec
``RULE_SEED`` (verrouillé par
``tests/regression/test_validation_rules_seeded.py``).

Idempotente : n'insère que les règles absentes. Rejouable sans effet sur une
base déjà correcte (dev, test, toute base reconstruite).

Revision ID: 20260904_0142
Revises: 20260903_0141
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260904_0142"
down_revision = "20260903_0141"
branch_labels = None
depends_on = None


# Instantané figé des trois règles QHSE, aligné sur ``RULE_SEED`` au 2026-09-04.
QHSE_RULES: tuple[dict[str, object], ...] = (
    {
        "rule_id": "RQ01",
        "domain": "Cohérence dates",
        "description": (
            "Date de clôture antérieure à la date d'émission — donnée de test/erreur de saisie."
        ),
        "default_severity": "bloquant",
        "scope": "qhse",
        "active": True,
    },
    {
        "rule_id": "RQ02",
        "domain": "Hygiène de saisie",
        "description": "Sujet/description correspondant à un motif de test (test/essai/demo).",
        "default_severity": "warning",
        "scope": "qhse",
        "active": True,
    },
    {
        "rule_id": "RQ03",
        "domain": "Identité navire",
        "description": (
            "Navire non résolu vers le référentiel MyTOWT existant lors de l'ingestion."
        ),
        "default_severity": "bloquant",
        "scope": "qhse",
        "active": True,
    },
)


def _rules_table() -> sa.Table:
    """Table allégée — jamais le modèle ORM (une migration ne suit pas le code)."""
    return sa.table(
        "validation_rules",
        sa.column("rule_id", sa.String),
        sa.column("domain", sa.String),
        sa.column("description", sa.Text),
        sa.column("default_severity", sa.String),
        sa.column("scope", sa.String),
        sa.column("active", sa.Boolean),
    )


def upgrade() -> None:
    rules = _rules_table()
    conn = op.get_bind()
    wanted = [r["rule_id"] for r in QHSE_RULES]
    existing = set(
        conn.execute(sa.select(rules.c.rule_id).where(rules.c.rule_id.in_(wanted))).scalars().all()
    )
    missing = [dict(r) for r in QHSE_RULES if r["rule_id"] not in existing]
    if missing:
        op.bulk_insert(rules, missing)


def downgrade() -> None:
    # No-op assumé. ``quality_check_results.rule_id`` est en ``ON DELETE
    # CASCADE`` : supprimer ces trois règles effacerait **les résultats de
    # contrôle qualité déjà produits**, c'est-à-dire un registre de constats,
    # pour défaire un simple peuplement de référentiel. Le coût du retour
    # arrière serait très supérieur à celui de laisser trois lignes de
    # catalogue inertes en place.
    pass
