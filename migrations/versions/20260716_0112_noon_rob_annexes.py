"""Add nav_event_noon.rob_uree_t / rob_eau_douce_t — ROB annexes (G5).

Colonnes additives (nullable) : ROB urée et ROB eau douce, exigés à la
déclaration d'un Noon mais indépendants du calcul carburant (jamais lus par
inter_event_compute/emission_ledger, ni par la chaîne ROB R14/IR02) — un
régime purement informatif, distinct du ROB de référence (PortCallEvent.rob_t).

Revision ID: 20260716_0112
Revises: 20260716_0111
Create Date: 2026-07-16

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260716_0112"
down_revision = "20260716_0111"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nav_event_noon", sa.Column("rob_uree_t", sa.Numeric(8, 3), nullable=True))
    op.add_column("nav_event_noon", sa.Column("rob_eau_douce_t", sa.Numeric(8, 3), nullable=True))


def downgrade():
    op.drop_column("nav_event_noon", "rob_eau_douce_t")
    op.drop_column("nav_event_noon", "rob_uree_t")
