"""ESC-04 — intervenant sur les opérations d'escale

Revision ID: 20260623_0071
Revises: 20260623_0070
Create Date: 2026-06-23 09:00:00

Reprise V2 : réintroduit le champ ``intervenant`` (nom/société réalisant
l'opération) sur ``escale_operations``. Les durées prévue/réelle sont
dérivées des bornes (planned_/actual_start/end) côté modèle — pas de
colonne. Colonne additive nullable → migration sûre.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260623_0071"
down_revision: Union[str, None] = "20260623_0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("escale_operations", sa.Column("intervenant", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("escale_operations", "intervenant")
