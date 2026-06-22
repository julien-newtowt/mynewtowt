"""booking goods arrival at loading port milestone

Revision ID: 20260622_0058
Revises: 20260622_0057
Create Date: 2026-06-22 13:00:00

Jalon « arrivée de la marchandise au port de chargement » pour la timeline
d'expédition (page Label Anemos).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0058"
down_revision: Union[str, None] = "20260622_0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bookings", sa.Column("goods_arrived_pol_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("bookings", "goods_arrived_pol_at")
