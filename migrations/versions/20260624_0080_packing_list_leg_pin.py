"""Pin packing_lists.leg_id at creation for per-leg PL/BL stability (COM-11).

Une commande ventilée multi-legs voit ``order.leg_id`` basculer après une
réaffectation partielle ; pour que la packing list / le BL restent rattachés à
leur leg d'origine, on épingle ``packing_lists.leg_id`` à la création. Colonne
nullable (lignes héritées = repli dynamique sur ``order/booking.leg_id``) →
changement additif sûr.

Revision ID: 20260624_0080
Revises: 20260624_0079
Create Date: 2026-06-24 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260624_0080"
down_revision = "20260624_0079"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "packing_lists",
        sa.Column("leg_id", sa.Integer(), sa.ForeignKey("legs.id"), nullable=True),
    )
    op.create_index("ix_packing_lists_leg_id", "packing_lists", ["leg_id"])


def downgrade():
    op.drop_index("ix_packing_lists_leg_id", table_name="packing_lists")
    op.drop_column("packing_lists", "leg_id")
