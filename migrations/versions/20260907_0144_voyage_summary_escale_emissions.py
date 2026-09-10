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

Le cas du **mouillage** est traité par la migration suivante
(``20260907_0145``) : constat métier du 2026-09-04, il est **hors périmètre
MRV**, et son émission est calculée pour l'analyse interne seulement.

Le résumé étant un **cache recalculable** (``refresh_summary``), ces colonnes
naissent à ``NULL`` : aucun backfill n'est tenté ici — une migration ne doit pas
dépendre du code de calcul du moment. Elles se remplissent ensuite de deux
façons, et de deux seulement :

1. au prochain recalcul déclenché par le hook de finalisation/validation d'un
   événement ;
2. 🔴 pour les voyages **antérieurs au déploiement**, par la reprise à froid
   ``python -m scripts.backfill_voyage_emission_summaries --yes`` — **sans
   filtre**. ``--missing-only`` ne prend que les voyages *sans résumé du tout* :
   il ne remplirait donc jamais une colonne neuve sur un résumé **existant**,
   qui est précisément le cas à réparer ici.

Le point 2 n'est pas optionnel : sans lui, ces voyages garderaient ``NULL``
pour toujours (aucun événement nouveau ne les concerne) et
``/mrv/emissions/port`` afficherait « non calculé » sur chacune de leurs
lignes.

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
