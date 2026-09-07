"""MRV — émissions du séjour au port (« Port Emissions ») dans le résumé par voyage.

Décision du 2026-09-04 (Yasmin) : **« port emissions = émissions d'escale »**.

Contexte. L'assiette des émissions du grand livre était la consommation **hors
mouillage** (``emission_ledger``, ``do_consumed = conso_hors``) : la
consommation d'escale était calculée et stockée (``conso_escale_t``, G12) mais
**aucune émission n'en était dérivée**. L'écran « Port Emissions » n'avait donc
aucun chiffre à afficher, et la règle d'or interdit de le calculer ailleurs que
dans ``services/emission_ledger.py``.

Ces deux colonnes matérialisent le calcul ajouté au grand livre, au même
facteur et par la même primitive (``emissions_breakdown``).

⚠️ **Assiette disjointe, jamais additionnée en silence.** ``co2_t`` porte le
trajet ; ``co2_escale_t`` porte l'escale qui **suit** l'arrivée du voyage — et
cette escale peut s'étendre sur la fenêtre du voyage suivant. Tout agrégat qui
voudrait un total « trajet + escale » doit le dire explicitement.

⚠️ **Reste non calculé** : la consommation au **mouillage**
(``conso_mouillage_t``) est exclue de l'assiette du trajet et ne reçoit toujours
aucune émission. C'est le cas symétrique de celui corrigé ici ; il n'a pas été
tranché et reste au backlog.

Le résumé étant un **cache recalculable** (``refresh_summary``), les colonnes
sont laissées à ``NULL`` : elles se remplissent au prochain recalcul (hook de
finalisation/validation d'événement, ou à la demande). Aucun backfill n'est
tenté ici — une migration ne doit pas dépendre du code de calcul du moment.

Renumérotée le 2026-09-07 (``20260904_0143`` → ``20260907_0144``)
-----------------------------------------------------------------
Cette migration se chaînait d'abord sur ``20260903_0141``, la tête de `main` au
démarrage de la branche. Pendant les trois jours d'attente de revue, `main` a
reçu la migration commerciale ``20260904_0141`` puis une révision de fusion
``20260904_0142``, et la PR #197 a été renumérotée en ``20260907_0143``.

La chaîne est donc désormais **linéaire par construction** :
``20260904_0142`` (fusion sur `main`) → ``20260907_0143`` (#197) →
``20260907_0144`` (celle-ci) → ``20260907_0145`` (mouillage). Les trois lots
étant empilés dans leur ordre de fusion, aucune nouvelle collision de tête
n'est possible.

Re-chaîner ainsi est un **commit vers l'avant**, pas une réécriture
d'historique — aucun force push (cf. `CLAUDE.md`, Git Workflow).

Revision ID: 20260907_0144
Revises: 20260907_0143
Create Date: 2026-09-04 (renumérotée le 2026-09-07)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260907_0144"
down_revision = "20260907_0143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voyage_emission_summaries",
        sa.Column("co2_escale_t", sa.Numeric(18, 6), nullable=True),
    )
    op.add_column(
        "voyage_emission_summaries",
        sa.Column("co2eq_escale_t", sa.Numeric(18, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("voyage_emission_summaries", "co2eq_escale_t")
    op.drop_column("voyage_emission_summaries", "co2_escale_t")
