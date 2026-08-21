"""MRV G1 — scission du Carbon Report au Cut-off (période avant/après).

Ajoute ``env_reports.period_seq`` (nullable) : NULL pour un rapport non
scindé (comportement historique de tous les types) ; 1/2 pour un Carbon
Report scindé au Cut-off (CDC v0.7 §9.2), permettant 2 ``EnvReport``
indépendants pour le même ``leg_id``.

Revision ID: 20260715_0107
Revises: 20260715_0106
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260715_0107"
down_revision = "20260715_0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("env_reports", sa.Column("period_seq", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("env_reports", "period_seq")
