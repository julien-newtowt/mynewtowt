"""grilles tarifaires multi-routes (Module 6) — rebuild propre du schéma

Refonte du modèle de grille en MULTI-ROUTES (1 grille = 1 client/défaut + 1
période + N routes) :
- ``rate_grids`` : retire ``pol_locode`` / ``pod_locode`` / ``base_rate_per_palette``
  (route + tarif descendent sur les lignes) ; ajoute ``vessel_id`` (lookup OPEX),
  ``bl_fee`` / ``booking_fee`` (forfaits) et ``brackets_json`` (brackets de volume
  au niveau grille) ;
- ``rate_grid_lines`` : redéfini = **routes** (POL/POD, distance, nav_days, OPEX
  jour, base_rate, surcharge manuelle) — drop/recreate (pas de reprise de données).

Revision ID: 20260619_0054
Revises: 20260618_0053
Create Date: 2026-06-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260619_0054"
down_revision = "20260618_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── rate_grid_lines : ancien schéma (brackets) → nouveau (routes) ──
    op.drop_table("rate_grid_lines")
    op.create_table(
        "rate_grid_lines",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "grid_id",
            sa.Integer(),
            sa.ForeignKey("rate_grids.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pol_locode", sa.String(length=5), nullable=False),
        sa.Column("pod_locode", sa.String(length=5), nullable=False),
        sa.Column("leg_id", sa.Integer(), sa.ForeignKey("legs.id"), nullable=True),
        sa.Column("distance_nm", sa.Numeric(8, 2), nullable=False),
        sa.Column("nav_days", sa.Numeric(8, 3), nullable=False),
        sa.Column("opex_daily", sa.Numeric(12, 2), nullable=False),
        sa.Column("base_rate", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "is_manual", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_index("ix_rate_grid_lines_grid_id", "rate_grid_lines", ["grid_id"])
    op.create_index("ix_rate_grid_lines_pol_locode", "rate_grid_lines", ["pol_locode"])
    op.create_index("ix_rate_grid_lines_pod_locode", "rate_grid_lines", ["pod_locode"])

    # ── rate_grids : en-tête multi-routes ──
    op.drop_column("rate_grids", "pol_locode")
    op.drop_column("rate_grids", "pod_locode")
    op.drop_column("rate_grids", "base_rate_per_palette")
    op.add_column(
        "rate_grids",
        sa.Column("vessel_id", sa.Integer(), sa.ForeignKey("vessels.id"), nullable=True),
    )
    op.create_index("ix_rate_grids_vessel_id", "rate_grids", ["vessel_id"])
    op.add_column("rate_grids", sa.Column("bl_fee", sa.Numeric(10, 2), nullable=True))
    op.add_column("rate_grids", sa.Column("booking_fee", sa.Numeric(10, 2), nullable=True))
    op.add_column("rate_grids", sa.Column("brackets_json", sa.Text(), nullable=True))


def downgrade() -> None:
    # ── rate_grids : retour à l'en-tête mono-route ──
    op.drop_column("rate_grids", "brackets_json")
    op.drop_column("rate_grids", "booking_fee")
    op.drop_column("rate_grids", "bl_fee")
    op.drop_index("ix_rate_grids_vessel_id", table_name="rate_grids")
    op.drop_column("rate_grids", "vessel_id")
    op.add_column(
        "rate_grids",
        sa.Column(
            "base_rate_per_palette",
            sa.Numeric(10, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column("rate_grids", sa.Column("pod_locode", sa.String(length=5), nullable=True))
    op.add_column("rate_grids", sa.Column("pol_locode", sa.String(length=5), nullable=True))
    op.create_index("ix_rate_grids_pol_locode", "rate_grids", ["pol_locode"])
    op.create_index("ix_rate_grids_pod_locode", "rate_grids", ["pod_locode"])

    # ── rate_grid_lines : routes → ancien schéma (brackets) ──
    op.drop_index("ix_rate_grid_lines_pod_locode", table_name="rate_grid_lines")
    op.drop_index("ix_rate_grid_lines_pol_locode", table_name="rate_grid_lines")
    op.drop_index("ix_rate_grid_lines_grid_id", table_name="rate_grid_lines")
    op.drop_table("rate_grid_lines")
    op.create_table(
        "rate_grid_lines",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "grid_id",
            sa.Integer(),
            sa.ForeignKey("rate_grids.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bracket_key", sa.String(length=20), nullable=False),
        sa.Column("bracket_label", sa.String(length=80), nullable=False),
        sa.Column("max_qty", sa.Integer(), nullable=False),
        sa.Column("coeff", sa.Numeric(6, 4), nullable=False),
    )
    op.create_index("ix_rate_grid_lines_grid_id", "rate_grid_lines", ["grid_id"])
