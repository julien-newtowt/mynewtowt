"""Widen co2_variables.value precision for small NOx/SOx factors (ADM-06).

Les facteurs d'émission NOx / SOx (kg par tonne-mille nautique) descendent
jusqu'à ~1e-5 (ex. ``sail_sox_per_tnm = 0.00001056``, 8 décimales). La colonne
``Numeric(12, 6)`` les arrondissait à la 6ᵉ décimale lors d'un seed/init admin
(jusqu'à ~4 % d'écart sur le facteur SOx voile). On élargit à ``Numeric(15, 9)``
pour rendre ces facteurs réellement éditables sans perte. Changement additif
(élargissement) : les valeurs CO₂ existantes (≤ 6 décimales) sont préservées.

Revision ID: 20260624_0077
Revises: 20260624_0076
Create Date: 2026-06-24 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260624_0077"
down_revision = "20260624_0076"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "co2_variables",
        "value",
        existing_type=sa.Numeric(12, 6),
        type_=sa.Numeric(15, 9),
        existing_nullable=False,
    )


def downgrade():
    op.alter_column(
        "co2_variables",
        "value",
        existing_type=sa.Numeric(15, 9),
        type_=sa.Numeric(12, 6),
        existing_nullable=False,
    )
