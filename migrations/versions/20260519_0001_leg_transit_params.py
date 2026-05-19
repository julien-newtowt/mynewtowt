"""legs.transit_speed_kn + elongation_coef (per-leg overrides of vessel defaults)

Revision ID: 20260519_0001
Revises: 20260518_0006
Create Date: 2026-05-19 09:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260519_0001"
down_revision: Union[str, None] = "20260518_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("legs", sa.Column("transit_speed_kn", sa.Float))
    op.add_column("legs", sa.Column("elongation_coef", sa.Float))


def downgrade() -> None:
    op.drop_column("legs", "elongation_coef")
    op.drop_column("legs", "transit_speed_kn")
