"""planning share extra filters (POL/POD + date range)

Revision ID: 20260622_0056
Revises: 20260622_0055
Create Date: 2026-06-22 11:00:00

Ajoute à ``planning_shares`` un filtrage par port de départ (POL), port
d'arrivée (POD) et une période de filtrage (date_from / date_to).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0056"
down_revision: Union[str, None] = "20260622_0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("planning_shares", sa.Column("pol_port_id", sa.Integer, sa.ForeignKey("ports.id")))
    op.add_column("planning_shares", sa.Column("pod_port_id", sa.Integer, sa.ForeignKey("ports.id")))
    op.add_column("planning_shares", sa.Column("date_from", sa.DateTime(timezone=True)))
    op.add_column("planning_shares", sa.Column("date_to", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("planning_shares", "date_to")
    op.drop_column("planning_shares", "date_from")
    op.drop_column("planning_shares", "pod_port_id")
    op.drop_column("planning_shares", "pol_port_id")
