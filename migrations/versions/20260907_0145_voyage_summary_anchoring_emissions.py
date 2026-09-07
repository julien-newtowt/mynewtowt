"""MRV — émissions au mouillage dans le résumé par voyage (🔴 HORS PÉRIMÈTRE MRV).

Constat métier du 2026-09-04 (Yasmin) : **le mouillage est hors périmètre MRV.**
La consommation au mouillage (``conso_mouillage_t``) était donc exclue de
l'assiette du trajet par construction (``do_consumed = conso_hors``) et ne
recevait aucune émission — c'est correct au regard du règlement, mais cela
laissait du carburant réellement brûlé sans émission connue, y compris pour
l'analyse interne.

Ces deux colonnes matérialisent le calcul ajouté au grand livre, au même
facteur et par la même primitive (``emissions_breakdown``) que les deux autres
assiettes : la règle d'or ne souffre pas d'exception, y compris pour un chiffre
hors MRV.

🔴 **Ce que ces colonnes ne sont pas.** Elles n'entrent dans **aucun** total
réglementaire. ``co2_t`` (trajet) et ``co2_escale_t`` (escale) forment le
périmètre MRV ; celles-ci vivent à côté. Toute restitution qui les inclut doit
être **opt-in** et porter la mention du périmètre — c'est le rôle du sélecteur
de ``/mrv/emissions/voyages``, dont le défaut reste le périmètre MRV seul.
Les additionner par défaut gonflerait un chiffre réglementaire, ce qui est
exactement l'erreur que cette séparation prévient.

Trois assiettes disjointes, donc, et jamais sommées en silence :
``conso_hors_mouillage_t`` (trajet, MRV) · ``conso_escale_t`` (escale, MRV) ·
``conso_mouillage_t`` (mouillage, hors MRV).

Le résumé étant un **cache recalculable** (``refresh_summary``), les colonnes
sont laissées à ``NULL`` : elles se remplissent au prochain recalcul. Aucun
backfill ici — une migration ne doit pas dépendre du code de calcul du moment.

Revision ID: 20260907_0145
Revises: 20260907_0144
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260907_0145"
down_revision = "20260907_0144"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voyage_emission_summaries",
        sa.Column("co2_mouillage_t", sa.Numeric(18, 6), nullable=True),
    )
    op.add_column(
        "voyage_emission_summaries",
        sa.Column("co2eq_mouillage_t", sa.Numeric(18, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("voyage_emission_summaries", "co2eq_mouillage_t")
    op.drop_column("voyage_emission_summaries", "co2_mouillage_t")
