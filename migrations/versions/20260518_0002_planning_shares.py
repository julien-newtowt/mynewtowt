"""planning shares table

Revision ID: 20260518_0002
Revises: 20260518_0001
Create Date: 2026-05-18 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260518_0002"
down_revision: Union[str, None] = "20260518_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "planning_shares",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.String(64), unique=True, nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("vessel_id", sa.Integer, sa.ForeignKey("vessels.id")),
        sa.Column("only_bookable", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_access_at", sa.DateTime(timezone=True)),
        sa.Column("access_count", sa.Integer, server_default="0", nullable=False),
    )
    op.create_index("ix_planning_shares_token", "planning_shares", ["token"])


def downgrade() -> None:
    op.drop_index("ix_planning_shares_token", table_name="planning_shares")
    op.drop_table("planning_shares")
