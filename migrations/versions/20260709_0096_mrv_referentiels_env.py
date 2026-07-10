"""MRV lot 1 — référentiels navire & facteurs d'émission multi-GES.

Socle paramétrable (H2/H3) de la refonte du reporting environnemental :

1. ``vessel_tanks`` / ``vessel_engines`` / ``vessel_hydrostatics`` — référentiel
   navire (cuves, moteurs avec groupe d'agrégation ME/AE, courbe hydrostatique
   pour la formule Cargo MRV EU 2016/1928). Schéma seulement : les seeds PAR
   NAVIRE (5 cuves + 6 moteurs pour ANEMOS/ARTEMIS) ne vivent PAS ici — les ids
   navires varient selon l'environnement — mais dans
   ``services.referential_env.ensure_vessel_env_defaults``, appelée de façon
   idempotente depuis l'écran ``/admin/flotte-env`` (bouton « initialiser »).
   ``vessel_hydrostatics`` reste vide (données officielles à obtenir, Q11).

2. ``emission_factors`` — facteurs d'émission multi-GES versionnés (CO₂/CH₄/
   N₂O TtW + WtT FuelEU), append-only comme ``co2_variables``. Seed
   **vessel-indépendant** : une unique ligne MDO (MEPC.391(81) + CFOTE_09).

3. Évolution ``vessels`` : ``lightweight_t``, ``default_fuel_type`` (défaut
   ``MDO``), ``water_density_default_t_m3``.

Revision ID: 20260709_0096
Revises: 20260706_0096
Create Date: 2026-07-09
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260709_0096"
down_revision = "20260706_0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Évolution vessels ───────────────────────────────────────────
    op.add_column(
        "vessels",
        sa.Column(
            "lightweight_t",
            sa.Numeric(10, 3),
            nullable=True,
            comment="Poids lège (lightweight) en tonnes",
        ),
    )
    op.add_column(
        "vessels",
        sa.Column(
            "default_fuel_type",
            sa.String(length=20),
            nullable=False,
            server_default="MDO",
            comment="Carburant par défaut (référentiel emission_factors)",
        ),
    )
    op.add_column(
        "vessels",
        sa.Column(
            "water_density_default_t_m3",
            sa.Numeric(8, 4),
            nullable=True,
            comment="Densité de l'eau par défaut (t/m³)",
        ),
    )

    # ── 2. Référentiel navire (cuves / moteurs / hydrostatiques) ───────
    op.create_table(
        "vessel_tanks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tank_code", sa.String(length=10), nullable=False),
        sa.Column("capacity_m3", sa.Numeric(10, 3), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_vessel_tanks_vessel_id", "vessel_tanks", ["vessel_id"])

    op.create_table(
        "vessel_engines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine_role", sa.String(length=30), nullable=False),
        sa.Column("engine_group", sa.String(length=10), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_vessel_engines_vessel_id", "vessel_engines", ["vessel_id"])

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

    # ── 3. Facteurs d'émission multi-GES (versionnés, append-only) ─────
    emission_factors = op.create_table(
        "emission_factors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fuel_type", sa.String(length=20), nullable=False, server_default="MDO"),
        sa.Column("ef_co2_kg_per_kg", sa.Numeric(15, 9), nullable=False),
        sa.Column("ef_ch4_kg_per_kg", sa.Numeric(15, 9), nullable=False),
        sa.Column("ef_n2o_kg_per_kg", sa.Numeric(15, 9), nullable=False),
        sa.Column("wtt_gco2eq_per_mj", sa.Numeric(15, 9), nullable=False),
        sa.Column("source_reference", sa.String(length=200), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_emission_factors_fuel_type", "emission_factors", ["fuel_type"])

    # Seed vessel-indépendant : facteur MDO courant (MEPC.391(81) + CFOTE_09
    # Rev02). Les référentiels PAR NAVIRE (cuves/moteurs) sont hors migration
    # (cf. docstring) — seedés via l'écran /admin/flotte-env.
    op.bulk_insert(
        emission_factors,
        [
            {
                "fuel_type": "MDO",
                "ef_co2_kg_per_kg": "3.206",
                "ef_ch4_kg_per_kg": "0.00005",
                "ef_n2o_kg_per_kg": "0.00018",
                "wtt_gco2eq_per_mj": "17.7",
                "source_reference": "MEPC.391(81) + CFOTE_09 Rev02",
                "valid_from": date(2025, 1, 1),
                "valid_to": None,
                "is_current": True,
                "created_by_id": None,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_emission_factors_fuel_type", table_name="emission_factors")
    op.drop_table("emission_factors")

    op.drop_index("ix_vessel_hydrostatics_vessel_id", table_name="vessel_hydrostatics")
    op.drop_table("vessel_hydrostatics")

    op.drop_index("ix_vessel_engines_vessel_id", table_name="vessel_engines")
    op.drop_table("vessel_engines")

    op.drop_index("ix_vessel_tanks_vessel_id", table_name="vessel_tanks")
    op.drop_table("vessel_tanks")

    op.drop_column("vessels", "water_density_default_t_m3")
    op.drop_column("vessels", "default_fuel_type")
    op.drop_column("vessels", "lightweight_t")
