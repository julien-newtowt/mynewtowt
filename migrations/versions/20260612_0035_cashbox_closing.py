"""Caisse de bord : clôture mensuelle + verrouillage + justificatifs.

- cashbox_movements : receipt_mime, closure_id (FK), locked_at.
- cashbox_closures  : period_start, movement_count, exported_at.

Revision ID: 20260612_0035
Revises: 20260612_0034
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0035"
down_revision = "20260612_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cashbox_movements", sa.Column("receipt_mime", sa.String(length=100), nullable=True))
    op.add_column("cashbox_movements", sa.Column("closure_id", sa.Integer(), nullable=True))
    op.add_column(
        "cashbox_movements", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_cashbox_movements_closure_id", "cashbox_movements", ["closure_id"]
    )
    op.create_foreign_key(
        "fk_cashbox_movements_closure",
        "cashbox_movements",
        "cashbox_closures",
        ["closure_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "cashbox_closures", sa.Column("period_start", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "cashbox_closures",
        sa.Column("movement_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "cashbox_closures", sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("cashbox_closures", "exported_at")
    op.drop_column("cashbox_closures", "movement_count")
    op.drop_column("cashbox_closures", "period_start")
    op.drop_constraint("fk_cashbox_movements_closure", "cashbox_movements", type_="foreignkey")
    op.drop_index("ix_cashbox_movements_closure_id", table_name="cashbox_movements")
    op.drop_column("cashbox_movements", "locked_at")
    op.drop_column("cashbox_movements", "closure_id")
    op.drop_column("cashbox_movements", "receipt_mime")
