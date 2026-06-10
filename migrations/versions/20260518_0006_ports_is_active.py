"""ports.is_active flag

Revision ID: 20260518_0006
Revises: 20260518_0005
Create Date: 2026-05-19 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260518_0006"
down_revision: Union[str, None] = "20260518_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ports",
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
    )
    op.create_index("ix_ports_is_active", "ports", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_ports_is_active", table_name="ports")
    op.drop_column("ports", "is_active")
