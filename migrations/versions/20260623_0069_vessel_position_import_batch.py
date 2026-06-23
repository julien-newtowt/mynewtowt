"""TRK-05 — traçabilité d'import des positions (import_batch + created_at)

Revision ID: 20260623_0069
Revises: 20260623_0068
Create Date: 2026-06-23 06:30:00

Reprise V2 : réintroduit sur ``vessel_positions`` le lot d'import (nom de
fichier d'origine) et la date d'insertion en base (distincte de l'instant de
mesure ``recorded_at``). Colonnes additives.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260623_0069"
down_revision: Union[str, None] = "20260623_0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vessel_positions", sa.Column("import_batch", sa.String(100), nullable=True))
    op.add_column(
        "vessel_positions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("vessel_positions", "created_at")
    op.drop_column("vessel_positions", "import_batch")
