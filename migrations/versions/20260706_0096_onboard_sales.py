"""Vente à bord — catalogue, stock, ventes & registre douanier.

Nouvelle zone « Vente à bord » de l'espace commandant : le commandant vend
des biens/services aux collaborateurs embarqués (régime avitaillement /
franchise). Quatre tables :

- ``onboard_products`` : catalogue global (prix, devise, unité).
- ``onboard_stock_movements`` : grand livre de stock par navire, append-only
  (= registre douanier), quantité signée.
- ``onboard_sales`` + ``onboard_sale_lines`` : ventes historisées, réglées en
  espèces (→ caisse de bord) ou par carte (Stripe Checkout). Le
  ``cashbox_movement_id`` verrouille l'idempotence du règlement.

Changement purement additif.

Revision ID: 20260706_0096
Revises: 20260706_0095
Create Date: 2026-07-09 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260706_0096"
down_revision = "20260706_0095"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "onboard_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="bien"),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False, server_default="EUR"),
        sa.Column("unit", sa.String(length=20), nullable=False, server_default="pièce"),
        sa.Column("tracks_stock", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku", name="uq_onboard_product_sku"),
    )

    op.create_table(
        "onboard_sales",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=20), nullable=False),
        sa.Column("vessel_id", sa.Integer(), nullable=False),
        sa.Column("leg_id", sa.Integer(), nullable=True),
        sa.Column("buyer_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("payment_method", sa.String(length=10), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=False, server_default="EUR"),
        sa.Column("total", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("regime", sa.String(length=20), nullable=False, server_default="franchise"),
        sa.Column("stripe_checkout_session_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("cashbox_movement_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["vessel_id"], ["vessels.id"]),
        sa.ForeignKeyConstraint(["leg_id"], ["legs.id"]),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["cashbox_movement_id"], ["cashbox_movements.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_onboard_sale_reference"),
        sa.UniqueConstraint(
            "stripe_checkout_session_id", name="uq_onboard_sale_stripe_session"
        ),
    )
    op.create_index("ix_onboard_sales_vessel_id", "onboard_sales", ["vessel_id"])
    op.create_index("ix_onboard_sales_leg_id", "onboard_sales", ["leg_id"])
    op.create_index("ix_onboard_sales_status", "onboard_sales", ["status"])

    op.create_table(
        "onboard_sale_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("qty", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["sale_id"], ["onboard_sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["onboard_products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sale_id", "product_id", name="uq_sale_line_product"),
    )
    op.create_index("ix_onboard_sale_lines_sale_id", "onboard_sale_lines", ["sale_id"])

    op.create_table(
        "onboard_stock_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vessel_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("recorded_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["vessel_id"], ["vessels.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["onboard_products.id"]),
        sa.ForeignKeyConstraint(["sale_id"], ["onboard_sales.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_onboard_stock_vessel_product",
        "onboard_stock_movements",
        ["vessel_id", "product_id"],
    )
    op.create_index("ix_onboard_stock_occurred", "onboard_stock_movements", ["occurred_at"])
    op.create_index(
        "ix_onboard_stock_movements_sale_id", "onboard_stock_movements", ["sale_id"]
    )


def downgrade():
    op.drop_table("onboard_stock_movements")
    op.drop_table("onboard_sale_lines")
    op.drop_index("ix_onboard_sales_status", table_name="onboard_sales")
    op.drop_index("ix_onboard_sales_leg_id", table_name="onboard_sales")
    op.drop_index("ix_onboard_sales_vessel_id", table_name="onboard_sales")
    op.drop_table("onboard_sales")
    op.drop_table("onboard_products")
