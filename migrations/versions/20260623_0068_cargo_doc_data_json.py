"""ONB-02 — champs structurés des documents cargo guidés (data_json)

Revision ID: 20260623_0068
Revises: 20260622_0067
Create Date: 2026-06-23 06:00:00

Reprise V2 : réintroduit le contenu structuré par type de document de bord
(NOR, LOP, Mate's Receipt…) dans une colonne JSON ``data_json`` sur
``cargo_documents``. Colonne nullable → migration additive et sûre (les
documents V3 existants, en texte libre ``body``, restent valides).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260623_0068"
down_revision: Union[str, None] = "20260622_0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cargo_documents", sa.Column("data_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cargo_documents", "data_json")
