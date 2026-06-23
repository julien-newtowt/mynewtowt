"""CARGO-13 — champs goods riches sur les batches (cases / units / valeur)

Revision ID: 20260623_0070
Revises: 20260623_0069
Create Date: 2026-06-23 08:00:00

Reprise V2 : réintroduit sur ``packing_list_batches`` le nombre de colis
(``cases_quantity``), les unités par colis (``units_per_case``) et la valeur
déclarée de la marchandise (``cargo_value_usd``). Les dimensions dérivées
(surface / volume / densité) sont calculées à la volée côté modèle — pas de
colonne. Colonnes additives nullable → migration sûre.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260623_0070"
down_revision: Union[str, None] = "20260623_0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("packing_list_batches", sa.Column("cases_quantity", sa.Integer(), nullable=True))
    op.add_column("packing_list_batches", sa.Column("units_per_case", sa.Integer(), nullable=True))
    op.add_column("packing_list_batches", sa.Column("cargo_value_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("packing_list_batches", "cargo_value_usd")
    op.drop_column("packing_list_batches", "units_per_case")
    op.drop_column("packing_list_batches", "cases_quantity")
