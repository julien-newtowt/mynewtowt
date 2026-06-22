"""port commercial closed days (Sat/Sun)

Revision ID: 20260622_0060
Revises: 20260622_0059
Create Date: 2026-06-22 15:00:00

Certains ports n'ont pas d'opérations commerciales le samedi et/ou le dimanche.
L'escale se décale alors vers le(s) jour(s) ouvré(s) suivant(s) (moteur de
planification).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0060"
down_revision: Union[str, None] = "20260622_0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "port_configs",
        sa.Column("closed_saturday", sa.Boolean, server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "port_configs",
        sa.Column("closed_sunday", sa.Boolean, server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("port_configs", "closed_sunday")
    op.drop_column("port_configs", "closed_saturday")
