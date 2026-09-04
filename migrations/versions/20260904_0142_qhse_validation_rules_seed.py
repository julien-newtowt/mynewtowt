"""Rattrapage du référentiel de validation — 7 règles + 11 seuils absents en prod.

Répare un défaut de production constaté le 2026-09-04 : **tout import QHSE
échouait en 500**, sans qu'aucune ligne ne soit écrite — et le même mécanisme
frappait la finalisation d'un événement MRV.

Chaîne du défaut
----------------
``quality_check_results.rule_id`` et ``validation_rule_thresholds.rule_id``
portent une FK vers ``validation_rules.rule_id``. Le socle a été semé en
production par la migration ``20260709_0097``, qui **importe les constantes
``RULE_SEED``/``THRESHOLD_SEED`` du code applicatif** au moment de son
exécution. Tout ce qui a été ajouté au catalogue *après* le passage de ``0097``
en production n'a donc jamais atteint la base : constaté à l'écran
``/mrv/parametres`` de production, le référentiel s'arrête à ``R26`` — 31
règles, l'état du 2026-07-09.

Manquent 7 règles : ``R27``-``R30`` (MRV, ajoutées les 15-16 juillet) et
``RQ01``-``RQ03`` (QHSE, ajoutées le 22 juillet, commit ``1145d73``). Tant
qu'une règle n'est pas exécutée, son absence est inoffensive ; dès qu'un
résultat la référence, l'``INSERT`` viole la FK et emporte la transaction
appelante — d'où le 500 de ``/qhse/import`` (scope ``qhse``) et l'échec de la
finalisation d'événement (``event_capture``, scope ``event`` : ``R28``-``R30``).

Les 11 seuils absents n'ont, eux, aucune conséquence de verdict :
``get_threshold`` retombe *fail-closed* sur les défauts codés, vérifiés
**identiques aux valeurs semées** (31/31, valeur et unité). Les insérer ne
change aucun résultat, cela rend seulement les valeurs visibles et éditables à
l'écran.

Aucune des 7 règles n'est ``bloquant`` hors scope ``qhse`` (5 ``warning``, 1
``info``) : ce rattrapage ne peut donc bloquer aucun workflow existant.

Pourquoi aucun test ne pouvait le voir
--------------------------------------
En dev et en test, ``seed_reference_data`` peuple les 38 règles au boot
(``app/main.py``). Et une base reconstruite depuis la chaîne complète de
migrations les a aussi, puisque ``0097`` relit le ``RULE_SEED`` **courant** —
vérifié empiriquement sur Postgres : chaîne neuve → 38 règles. Seule une base
migrée avant les ajouts est incomplète, et il n'en existait qu'une : la
production. Le défaut était **structurellement invisible à tout test**.

C'est la leçon de fond : **une migration qui importe une constante du code
applicatif n'est pas déterministe** — son effet dépend de la date à laquelle
elle s'exécute. Les valeurs ci-dessous sont donc écrites **en dur** : une
migration est un instantané, pas un appel au code vivant. Elles doivent rester
alignées avec le catalogue (verrouillé par
``tests/regression/test_validation_rules_seeded.py``).

Idempotente : n'insère que l'absent, sur la clé naturelle (``rule_id`` pour les
règles, ``(rule_id, vessel_id NULL, parameter_name)`` pour les seuils).
Rejouable sans effet sur une base déjà correcte.

Revision ID: 20260904_0142
Revises: 20260903_0141
Create Date: 2026-09-04
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260904_0142"
down_revision = "20260903_0141"
branch_labels = None
depends_on = None


# Instantané figé de l'écart mesuré entre le catalogue au moment de ``0097`` et
# le catalogue courant, au 2026-09-04.
CATCHUP_RULES: tuple[dict[str, object], ...] = (
    {
        "rule_id": "R27",
        "domain": "Cut-off fin d'année",
        "description": "Voyage en cours à la bascule d'année civile (31/12 24:00 UTC) sans "
        "événement Cut-off finalisé — bloque la consolidation MRV au-delà de "
        "tolerance_cutoff_h.",
        "default_severity": "warning",
        "scope": "voyage",
        "active": True,
    },
    {
        "rule_id": "R28",
        "domain": "Distance haversine vs loguée (SOSP)",
        "description": "Distance haversine calculée entre deux Noon consécutifs vs distance "
        "loguée par le bord (delta distance_from_sosp_nm) — sous-estimation "
        "systématique possible en flotte vélique (louvoiement), dégrade "
        "artificiellement l'EF_MRV affiché (Matrice §8, revue technique "
        "09/07). N'est jamais corrigée automatiquement.",
        "default_severity": "warning",
        "scope": "event",
        "active": True,
    },
    {
        "rule_id": "R29",
        "domain": "Complétude relevés Noon (voilure/température)",
        "description": "Voilure (sail_readings) et températures air/mer (hold_readings) "
        "manquantes sur un Noon — utiles à l'étude des conditions de transport "
        "et au calcul du profil de propulsion (G6, volet complétude "
        "originalement porté par R13, jamais codé).",
        "default_severity": "info",
        "scope": "event",
        "active": True,
    },
    {
        "rule_id": "R30",
        "domain": "ROB annexes Noon (urée/eau douce)",
        "description": "ROB urée/eau douce manquants sur un Noon — indépendants du calcul "
        "carburant, purement informatifs (G5, originalement porté par R11, "
        "jamais codé pour le nouveau modèle événementiel).",
        "default_severity": "warning",
        "scope": "event",
        "active": True,
    },
    {
        "rule_id": "RQ01",
        "domain": "Cohérence dates",
        "description": "Date de clôture antérieure à la date d'émission — donnée de "
        "test/erreur de saisie.",
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
        "description": "Navire non résolu vers le référentiel MyTOWT existant lors de "
        "l'ingestion.",
        "default_severity": "bloquant",
        "scope": "qhse",
        "active": True,
    },
)

CATCHUP_THRESHOLDS: tuple[dict[str, object], ...] = (
    {
        "rule_id": "R19",
        "vessel_id": None,
        "parameter_name": "delai_alerte_siege_brouillon_h",
        "value": "48",
        "unit": "h",
        "provisional": True,
        "note": "Délai d'alerte siège d'un brouillon non finalisé (2e seuil R19, " "proposition).",
    },
    {
        "rule_id": "R24",
        "vessel_id": None,
        "parameter_name": "fenetre_rattachement_bunker_j",
        "value": "25",
        "unit": "j",
        "provisional": True,
        "note": "Fenêtre de rattachement automatique du soutage au voyage suivant (au-delà : "
        "leg_id NULL, choix manuel possible).",
    },
    {
        "rule_id": "R04",
        "vessel_id": None,
        "parameter_name": "tolerance_datetime_futur_h",
        "value": "24",
        "unit": "h",
        "provisional": True,
        "note": "Tolérance d'un horodatage dans le futur avant alerte de plausibilité (R04).",
    },
    {
        "rule_id": "R10",
        "vessel_id": None,
        "parameter_name": "delai_confirmation_reset_j",
        "value": "3",
        "unit": "j",
        "provisional": True,
        "note": "Délai au-delà duquel une régression compteur non confirmée passe de warning "
        "(→ admin) à bloquant (escalade R10, Matrice §3).",
    },
    {
        "rule_id": "IR03",
        "vessel_id": None,
        "parameter_name": "ir03_min_reports_figes",
        "value": "3",
        "unit": "reports",
        "provisional": True,
        "note": "Nombre de relevés consécutifs à ROB strictement figé avant alerte (IR03 ; "
        "cas réel dossier : figé 4 j).",
    },
    {
        "rule_id": "IR03",
        "vessel_id": None,
        "parameter_name": "ir03_conso_min_t",
        "value": "0.05",
        "unit": "t",
        "provisional": True,
        "note": "Consommation minimale entre relevés au-delà de laquelle un ROB figé est "
        "incohérent (IR03 ; valeur notebook QC).",
    },
    {
        "rule_id": "IR05",
        "vessel_id": None,
        "parameter_name": "ir05_min_reports_figes",
        "value": "3",
        "unit": "reports",
        "provisional": True,
        "note": "Nombre de relevés consécutifs à position strictement figée en mer avant "
        "alerte (IR05).",
    },
    {
        "rule_id": "R12",
        "vessel_id": None,
        "parameter_name": "min_releves_meteo_jour",
        "value": "3",
        "unit": "relevés",
        "provisional": True,
        "note": "Nombre minimal de relevés météo horodatés (créneaux 4 h) attendus par "
        "NoonEvent — volet « fréquence » de R12 (Matrice §1), jamais codé jusqu'ici "
        "(G7).",
    },
    {
        "rule_id": "R27",
        "vessel_id": None,
        "parameter_name": "tolerance_cutoff_h",
        "value": "24",
        "unit": "h",
        "provisional": True,
        "note": "Délai de tolérance après la bascule d'année avant escalade bloquante de R27 "
        "(CDC v0.7 §14.1, proposition).",
    },
    {
        "rule_id": "R27",
        "vessel_id": None,
        "parameter_name": "rappel_cutoff_avant_j",
        "value": "7",
        "unit": "j",
        "provisional": True,
        "note": "Fenêtre de rappel au Master avant l'approche de la bascule d'année (CDC v0.7 "
        "§9.2 : « rappel système au Master à l'approche de l'échéance »), "
        "proposition.",
    },
    {
        "rule_id": "R28",
        "vessel_id": None,
        "parameter_name": "tolerance_distance_haversine_nm",
        "value": "20",
        "unit": "nm",
        "provisional": True,
        "note": "Écart acceptable entre distance haversine calculée et distance loguée (SOSP) "
        "— aucune valeur proposée par la Matrice §8 (« à confirmer avec le métier, "
        "nouveau »), alignée sur tolerance_distance_manuelle_nm (R09) à défaut d'un "
        "chiffre métier.",
    },
)


def _rules_table() -> sa.Table:
    """Tables allégées — jamais les modèles ORM (une migration ne suit pas le code)."""
    return sa.table(
        "validation_rules",
        sa.column("rule_id", sa.String),
        sa.column("domain", sa.String),
        sa.column("description", sa.Text),
        sa.column("default_severity", sa.String),
        sa.column("scope", sa.String),
        sa.column("active", sa.Boolean),
    )


def _thresholds_table() -> sa.Table:
    return sa.table(
        "validation_rule_thresholds",
        sa.column("rule_id", sa.String),
        sa.column("vessel_id", sa.Integer),
        sa.column("parameter_name", sa.String),
        sa.column("value", sa.Numeric),
        sa.column("unit", sa.String),
        sa.column("provisional", sa.Boolean),
        sa.column("note", sa.Text),
    )


def upgrade() -> None:
    conn = op.get_bind()

    rules = _rules_table()
    wanted_rules = [r["rule_id"] for r in CATCHUP_RULES]
    present_rules = set(
        conn.execute(sa.select(rules.c.rule_id).where(rules.c.rule_id.in_(wanted_rules)))
        .scalars()
        .all()
    )
    missing_rules = [dict(r) for r in CATCHUP_RULES if r["rule_id"] not in present_rules]
    if missing_rules:
        op.bulk_insert(rules, missing_rules)

    # Les seuils viennent après : leur FK pointe sur les règles ci-dessus.
    thresholds = _thresholds_table()
    present_thr = set(
        conn.execute(
            sa.select(thresholds.c.rule_id, thresholds.c.parameter_name).where(
                thresholds.c.vessel_id.is_(None)
            )
        ).all()
    )
    missing_thr = [
        {**t, "value": Decimal(str(t["value"]))}
        for t in CATCHUP_THRESHOLDS
        if (t["rule_id"], t["parameter_name"]) not in present_thr
    ]
    if missing_thr:
        op.bulk_insert(thresholds, missing_thr)


def downgrade() -> None:
    # No-op assumé. ``quality_check_results.rule_id`` est en ``ON DELETE
    # CASCADE`` : supprimer ces règles effacerait **les résultats de contrôle
    # déjà produits**, c'est-à-dire un registre de constats, pour défaire un
    # simple peuplement de référentiel. Le coût du retour arrière serait très
    # supérieur à celui de laisser des lignes de catalogue inertes en place.
    pass
