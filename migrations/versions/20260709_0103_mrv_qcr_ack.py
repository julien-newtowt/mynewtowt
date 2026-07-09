"""MRV lot 8 — acquittement des anomalies qualité (écran /mrv/qualite).

Ajoute à ``quality_check_results`` les 2 colonnes d'acquittement d'un ``fail``
par le siège :

- ``acknowledged_at`` — horodatage de l'acquittement ;
- ``acknowledged_by`` — FK users (SET NULL), auteur de l'acquittement.

Un fail acquitté ne re-déclenche plus d'alerte (dédup du routage,
cf. ``services.validation_rules_catalog.route_alerts``) ; le journal reste
append-only, seule l'action de traitement est datée/attribuée.

Revision ID: 20260709_0103
Revises: 20260709_0102
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260709_0103"
down_revision = "20260709_0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quality_check_results",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "quality_check_results",
        sa.Column(
            "acknowledged_by",
            sa.Integer(),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_qcr_acknowledged_by_users"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("quality_check_results", "acknowledged_by")
    op.drop_column("quality_check_results", "acknowledged_at")
