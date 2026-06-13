"""Escale lock (per-leg) + coût des opérations d'escale.

Actions correctives direction (audit) :
- FLX (verrou escale) : ajoute ``legs.escale_locked_at`` /
  ``legs.escale_locked_by`` — l'escale est verrouillée à la clôture, les
  endpoints de modification d'escale refusent alors toute écriture.
- FLX-05 (coûts escale → LegFinance) : ajoute
  ``escale_operations.cost_forecast`` / ``cost_actual`` afin que le rollup
  financier puisse sommer le coût prescrit des opérations d'escale.

Toutes les colonnes sont nullables (aucun backfill nécessaire).

Revision ID: 20260612_0027
Revises: 20260612_0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0027"
down_revision = "20260612_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Verrou escale (per-leg).
    op.add_column("legs", sa.Column("escale_locked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("legs", sa.Column("escale_locked_by", sa.String(length=100), nullable=True))

    # Coût des opérations d'escale (FLX-05).
    op.add_column("escale_operations", sa.Column("cost_forecast", sa.Float(), nullable=True))
    op.add_column("escale_operations", sa.Column("cost_actual", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("escale_operations", "cost_actual")
    op.drop_column("escale_operations", "cost_forecast")
    op.drop_column("legs", "escale_locked_by")
    op.drop_column("legs", "escale_locked_at")
