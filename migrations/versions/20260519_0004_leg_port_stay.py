"""legs.port_stay_planned_hours — durée d'escale à programmer

Revision ID: 20260519_0004
Revises: 20260519_0003
Create Date: 2026-05-19 14:00:00

Ajoute la notion de durée d'escale planifiée à l'arrivée de chaque leg
(en heures). Utilisé par le planning pour anticiper la fenêtre de quai
avant le prochain leg du même navire.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260519_0004"
down_revision: Union[str, None] = "20260519_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("legs", sa.Column("port_stay_planned_hours", sa.Integer))


def downgrade() -> None:
    op.drop_column("legs", "port_stay_planned_hours")
