"""MRV G10 — retrait de ``vessel_hydrostatics`` (mécanique de calcul dormante).

Le Cargo MRV (« deadweight carried », EU 2016/1928) est saisi directement par
le Master (décision CDC v0.7 du 09/07/2026). La table restait vide depuis sa
création (données hydrostatiques officielles jamais obtenues, Q11) et le seul
chemin de calcul qui la lisait (``inter_event_compute.compute_cargo_mrv``) est
retiré en même temps (G10) — plus aucun code ne la référence.

``Vessel.lightweight_t`` et ``Vessel.water_density_default_t_m3`` sont
conservés (attributs informatifs optionnels, CDC v0.7) : seule la table
``vessel_hydrostatics`` disparaît.

Revision ID: 20260715_0108
Revises: 20260715_0107
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260715_0108"
down_revision = "20260715_0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_vessel_hydrostatics_vessel_id", table_name="vessel_hydrostatics")
    op.drop_table("vessel_hydrostatics")


def downgrade() -> None:
    op.create_table(
        "vessel_hydrostatics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("draft_m", sa.Numeric(8, 3), nullable=False),
        sa.Column("displacement_m3", sa.Numeric(12, 3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_vessel_hydrostatics_vessel_id", "vessel_hydrostatics", ["vessel_id"])
