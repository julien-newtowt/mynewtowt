"""devis — ajustement commercial (remise/majoration + commentaire) + historique des consultations

Revision ID: 20260618_0044
Revises: 20260617_0043
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260618_0044"
down_revision = "20260617_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quotes",
        sa.Column(
            "adjustment_eur",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("quotes", sa.Column("adjustment_comment", sa.Text(), nullable=True))

    op.create_table(
        "quote_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "quote_id",
            sa.Integer(),
            sa.ForeignKey("quotes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "viewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("viewer", sa.String(20), nullable=False, server_default="client"),
        sa.Column("ip_address", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("quote_views")
    op.drop_column("quotes", "adjustment_comment")
    op.drop_column("quotes", "adjustment_eur")
