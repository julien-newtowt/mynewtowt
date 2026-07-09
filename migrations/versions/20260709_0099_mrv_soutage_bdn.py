"""MRV lot 6 — soutage (Bunker Report / BDN) et allocations par cuve.

Crée :
- ``bunker_operations`` — en-tête d'un soutage (Bunker Delivery Note) :
  rattachement voyage calculé (``leg_id`` nullable, cf.
  ``services.bunkering.resolve_leg_for_bunker``), propriétés carburant du
  BDN, cycle de vie ``brouillon``/``valide_master``, unicité ``bdn_number``.
- ``bunker_tank_allocations`` — répartition par cuve (``vessel_tanks``, lot 1)
  d'un soutage, UNIQUE(bunker_id, tank_id).

NOTE ORCHESTRATION — ``down_revision`` provisoire
--------------------------------------------------
Ce lot (L6 — soutage) a été développé **en parallèle** du lot 3 (modèle
événementiel ``nav_events``) sur une branche isolée. Le lot 3 crée la
migration ``20260709_0098`` sur SA branche ; dans CE worktree, cette
migration n'existe pas encore, donc ``down_revision`` pointe directement sur
``20260709_0097`` (dernière migration visible ici, lot 2 — socle du moteur de
règles). **Au merge des deux lots**, l'orchestrateur doit rechaîner cette
migration 0099 pour qu'elle *revise* ``20260709_0098`` (pas ``0097``) — sans
quoi Alembic verrait deux têtes (``0098`` et ``0099`` toutes deux filles de
``0097``). Aucune autre modification n'est nécessaire pour ce rechaînage
(``bunker_operations``/``bunker_tank_allocations`` ne référencent aucune
table du lot 3). Dans ce worktree isolé, la chaîne locale reste 0096→0097→
0099 : c'est attendu (cf. consigne de tâche).

Revision ID: 20260709_0099
Revises: 20260709_0097
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260709_0099"
down_revision = "20260709_0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bunker_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bdn_number", sa.String(length=40), nullable=False, unique=True),
        sa.Column("port_locode", sa.String(length=5), nullable=False),
        sa.Column("delivery_datetime_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "fuel_type", sa.String(length=20), nullable=False, server_default="MDO"
        ),
        sa.Column("mass_t", sa.Numeric(10, 3), nullable=False),
        sa.Column("sulfur_content_pct", sa.Numeric(6, 3), nullable=True),
        sa.Column("density_15c_t_m3", sa.Numeric(8, 4), nullable=False),
        sa.Column("viscosity_cst", sa.Numeric(8, 2), nullable=True),
        sa.Column("water_content_pct", sa.Numeric(6, 3), nullable=True),
        sa.Column("lower_heating_value", sa.Numeric(10, 4), nullable=True),
        sa.Column("higher_heating_value", sa.Numeric(10, 4), nullable=True),
        sa.Column("ef_ttw_co2", sa.Numeric(12, 6), nullable=True),
        sa.Column("supplier_name", sa.String(length=200), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="brouillon"
        ),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_saved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("validated_master_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "validated_master_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_bunkerop_vessel", "bunker_operations", ["vessel_id"])
    op.create_index("ix_bunkerop_leg", "bunker_operations", ["leg_id"])
    op.create_index("ix_bunkerop_status", "bunker_operations", ["status"])
    op.create_index(
        "ix_bunkerop_delivery", "bunker_operations", ["delivery_datetime_utc"]
    )

    op.create_table(
        "bunker_tank_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "bunker_id",
            sa.Integer(),
            sa.ForeignKey("bunker_operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tank_id",
            sa.Integer(),
            sa.ForeignKey("vessel_tanks.id"),
            nullable=False,
        ),
        sa.Column("volume_m3", sa.Numeric(10, 3), nullable=False),
        sa.Column("density_t_m3", sa.Numeric(8, 4), nullable=False),
        sa.UniqueConstraint("bunker_id", "tank_id", name="uq_bunkertank_bunker_tank"),
    )
    op.create_index("ix_bunkertank_bunker", "bunker_tank_allocations", ["bunker_id"])
    op.create_index("ix_bunkertank_tank", "bunker_tank_allocations", ["tank_id"])


def downgrade() -> None:
    op.drop_index("ix_bunkertank_tank", table_name="bunker_tank_allocations")
    op.drop_index("ix_bunkertank_bunker", table_name="bunker_tank_allocations")
    op.drop_table("bunker_tank_allocations")

    op.drop_index("ix_bunkerop_delivery", table_name="bunker_operations")
    op.drop_index("ix_bunkerop_status", table_name="bunker_operations")
    op.drop_index("ix_bunkerop_leg", table_name="bunker_operations")
    op.drop_index("ix_bunkerop_vessel", table_name="bunker_operations")
    op.drop_table("bunker_operations")
