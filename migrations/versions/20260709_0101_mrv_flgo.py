"""MRV lot 7 — intégration FLGO (Marad, lecture seule) : relevés fuel/gas-oil.

Crée :
- ``flgo_readings`` — relevé FLGO (jaugeage "measurement" / réception
  "received"), importé depuis l'API Marad ou un import xlsx de repli.
  Anti-doublon naturel : ``UNIQUE(vessel_id, reading_datetime, action_type,
  product_name)``.
- ``flgo_tank_compartment_volumes`` — détail par compartiment physique d'un
  relevé, ``tank_code`` dérivé (correspondance directe avec
  ``vessel_tanks.tank_code``, lot 1).
- ``flgo_voyage_consumption_refs`` — contrôle croisé indépendant conso ME/AE
  par voyage (schéma seul dans ce lot, cf. app/models/flgo.py).


Revision ID: 20260709_0101
Revises: 20260709_0100
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260709_0101"
down_revision = "20260709_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flgo_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("product_name", sa.String(length=80), nullable=False),
        sa.Column("reading_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_volume_m3", sa.Numeric(10, 3), nullable=False),
        sa.Column("total_rob_m3", sa.Numeric(10, 3), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "vessel_id",
            "reading_datetime",
            "action_type",
            "product_name",
            name="uq_flgoreading_natural_key",
        ),
    )
    op.create_index("ix_flgoreading_vessel", "flgo_readings", ["vessel_id"])
    op.create_index("ix_flgoreading_datetime", "flgo_readings", ["reading_datetime"])
    op.create_index("ix_flgoreading_action_type", "flgo_readings", ["action_type"])

    op.create_table(
        "flgo_tank_compartment_volumes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "flgo_reading_id",
            sa.Integer(),
            sa.ForeignKey("flgo_readings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("compartment_code", sa.String(length=120), nullable=False),
        sa.Column("tank_code", sa.String(length=10), nullable=False),
        sa.Column("volume_m3", sa.Numeric(10, 3), nullable=False),
        sa.Column("mass_t", sa.Numeric(10, 3), nullable=True),
    )
    op.create_index(
        "ix_flgotankvol_reading", "flgo_tank_compartment_volumes", ["flgo_reading_id"]
    )
    op.create_index(
        "ix_flgotankvol_tank_code", "flgo_tank_compartment_volumes", ["tank_code"]
    )

    op.create_table(
        "flgo_voyage_consumption_refs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("me_consumption_t", sa.Numeric(10, 3), nullable=False),
        sa.Column("ae_consumption_t", sa.Numeric(10, 3), nullable=False),
        sa.Column("ecart_t", sa.Numeric(10, 3), nullable=True),
    )
    op.create_index("ix_flgovoyageref_leg", "flgo_voyage_consumption_refs", ["leg_id"])


def downgrade() -> None:
    op.drop_index("ix_flgovoyageref_leg", table_name="flgo_voyage_consumption_refs")
    op.drop_table("flgo_voyage_consumption_refs")

    op.drop_index("ix_flgotankvol_tank_code", table_name="flgo_tank_compartment_volumes")
    op.drop_index("ix_flgotankvol_reading", table_name="flgo_tank_compartment_volumes")
    op.drop_table("flgo_tank_compartment_volumes")

    op.drop_index("ix_flgoreading_action_type", table_name="flgo_readings")
    op.drop_index("ix_flgoreading_datetime", table_name="flgo_readings")
    op.drop_index("ix_flgoreading_vessel", table_name="flgo_readings")
    op.drop_table("flgo_readings")
