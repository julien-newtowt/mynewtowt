"""Grilles tarifaires par route + options + devis publics.

- rate_grids : porte désormais la route (pol/pod), le flag is_default,
  et accepte client_id NULL (grille par défaut d'une route).
- rate_grid_options : options tarifaires d'une grille (palette / tonne /
  réservation / booking note).
- quotes + quote_lines : devis générés par l'outil public /devis.
- client_accounts.commercial_client_id : lien compte client ↔ client
  commercial (résolution de la grille négociée).

Revision ID: 20260612_0024
Revises: 20260612_0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0024"
down_revision = "20260612_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- rate_grids : route + défaut + client nullable -------------------
    op.add_column("rate_grids", sa.Column("pol_locode", sa.String(length=5), nullable=True))
    op.add_column("rate_grids", sa.Column("pod_locode", sa.String(length=5), nullable=True))
    op.add_column(
        "rate_grids",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_rate_grids_pol_locode", "rate_grids", ["pol_locode"])
    op.create_index("ix_rate_grids_pod_locode", "rate_grids", ["pod_locode"])
    op.create_index("ix_rate_grids_is_default", "rate_grids", ["is_default"])
    op.alter_column("rate_grids", "client_id", existing_type=sa.Integer(), nullable=True)

    # --- rate_grid_options ------------------------------------------------
    op.create_table(
        "rate_grid_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "grid_id",
            sa.Integer(),
            sa.ForeignKey("rate_grids.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("amount_eur", sa.Numeric(10, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_rate_grid_options_grid_id", "rate_grid_options", ["grid_id"])

    # --- quotes ------------------------------------------------------------
    op.create_table(
        "quotes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference", sa.String(length=24), nullable=False, unique=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="issued"),
        sa.Column("pol_locode", sa.String(length=5), nullable=False),
        sa.Column("pod_locode", sa.String(length=5), nullable=False),
        sa.Column(
            "leg_id", sa.Integer(), sa.ForeignKey("legs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("etd_snapshot", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "grid_id",
            sa.Integer(),
            sa.ForeignKey("rate_grids.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("grid_reference", sa.String(length=20), nullable=True),
        sa.Column(
            "client_account_id",
            sa.Integer(),
            sa.ForeignKey("client_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("contact_name", sa.String(length=160), nullable=True),
        sa.Column("contact_email", sa.String(length=254), nullable=True),
        sa.Column("contact_company", sa.String(length=200), nullable=True),
        sa.Column("palettes_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tonnage_t", sa.Numeric(10, 3), nullable=True),
        sa.Column("hazardous", sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EUR"),
        sa.Column("freight_subtotal_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("options_total_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("lang", sa.String(length=5), nullable=False, server_default="fr"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_quotes_pol_locode", "quotes", ["pol_locode"])
    op.create_index("ix_quotes_pod_locode", "quotes", ["pod_locode"])
    op.create_index("ix_quotes_client_account_id", "quotes", ["client_account_id"])

    op.create_table(
        "quote_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "quote_id",
            sa.Integer(),
            sa.ForeignKey("quotes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False, server_default="1"),
        sa.Column("unit_price_eur", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("total_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.create_index("ix_quote_lines_quote_id", "quote_lines", ["quote_id"])

    # --- client_accounts.commercial_client_id ------------------------------
    op.add_column(
        "client_accounts",
        sa.Column(
            "commercial_client_id",
            sa.Integer(),
            sa.ForeignKey("commercial_clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_client_accounts_commercial_client_id", "client_accounts", ["commercial_client_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_client_accounts_commercial_client_id", table_name="client_accounts")
    op.drop_column("client_accounts", "commercial_client_id")
    op.drop_index("ix_quote_lines_quote_id", table_name="quote_lines")
    op.drop_table("quote_lines")
    op.drop_index("ix_quotes_client_account_id", table_name="quotes")
    op.drop_index("ix_quotes_pod_locode", table_name="quotes")
    op.drop_index("ix_quotes_pol_locode", table_name="quotes")
    op.drop_table("quotes")
    op.drop_index("ix_rate_grid_options_grid_id", table_name="rate_grid_options")
    op.drop_table("rate_grid_options")
    op.alter_column("rate_grids", "client_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_rate_grids_is_default", table_name="rate_grids")
    op.drop_index("ix_rate_grids_pod_locode", table_name="rate_grids")
    op.drop_index("ix_rate_grids_pol_locode", table_name="rate_grids")
    op.drop_column("rate_grids", "is_default")
    op.drop_column("rate_grids", "pod_locode")
    op.drop_column("rate_grids", "pol_locode")
