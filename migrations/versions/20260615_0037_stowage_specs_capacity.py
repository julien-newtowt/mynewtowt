"""Arrimage : référentiel capacité/résistance par classe + remontée packing list.

Ajoute :
- ``vessels.vessel_class`` (classe de navire, ex. "phoenix") ;
- table ``stowage_zone_specs`` (capacité & résistance par zone et par classe) ;
- colonnes packing-list sur ``stowage_items`` (dimension, hauteur, classement,
  gerbage) — remontée de la packing list dans le plan de chargement.

Revision ID: 20260615_0037
Revises: 20260612_0036
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260615_0037"
down_revision = "20260612_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Classe de navire (pilote le référentiel d'arrimage).
    op.add_column(
        "vessels",
        sa.Column(
            "vessel_class",
            sa.String(length=40),
            nullable=False,
            server_default="phoenix",
        ),
    )

    # Remontée packing list → plan d'arrimage.
    op.add_column("stowage_items", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("stowage_items", sa.Column("hs_code", sa.String(length=20), nullable=True))
    op.add_column("stowage_items", sa.Column("imdg_class", sa.String(length=20), nullable=True))
    op.add_column("stowage_items", sa.Column("un_number", sa.String(length=10), nullable=True))
    op.add_column("stowage_items", sa.Column("length_cm", sa.Float(), nullable=True))
    op.add_column("stowage_items", sa.Column("width_cm", sa.Float(), nullable=True))
    op.add_column("stowage_items", sa.Column("height_cm", sa.Float(), nullable=True))
    op.add_column("stowage_items", sa.Column("cubage_m3", sa.Float(), nullable=True))
    op.add_column(
        "stowage_items",
        sa.Column("stackable", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "stowage_items",
        sa.Column("is_stacked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Référentiel capacité/résistance par zone et par classe de navire.
    op.create_table(
        "stowage_zone_specs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vessel_class", sa.String(length=40), nullable=False),
        sa.Column("zone", sa.String(length=20), nullable=False),
        sa.Column("capacity_epal", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("max_load_t", sa.Float(), nullable=True),
        sa.Column("max_pallet_weight_kg", sa.Float(), nullable=True),
        sa.Column("stack_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("heavy_stack_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("segregated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("vessel_class", "zone", name="uq_zone_spec_class_zone"),
    )
    op.create_index(
        "ix_stowage_zone_specs_vessel_class",
        "stowage_zone_specs",
        ["vessel_class"],
    )


def downgrade() -> None:
    op.drop_index("ix_stowage_zone_specs_vessel_class", table_name="stowage_zone_specs")
    op.drop_table("stowage_zone_specs")

    for col in (
        "is_stacked",
        "stackable",
        "cubage_m3",
        "height_cm",
        "width_cm",
        "length_cm",
        "un_number",
        "imdg_class",
        "hs_code",
        "description",
    ):
        op.drop_column("stowage_items", col)

    op.drop_column("vessels", "vessel_class")
