"""MRV G1 — événement Year-End Cut-off (CDC v0.7 §9.2/§10.1) + ROB par carburant.

Ajoute l'identité polymorphe ``cutoff`` à ``nav_events`` (pas de migration de
schéma requise pour ``event_type``, colonne ``String(20)`` sans CHECK/enum —
``CutoffEvent`` n'a aucune colonne propre, il partage la table mère comme
Departure/Arrival partagent ``nav_event_portcall``). Seule addition réelle :
``nav_event_rob_by_fuel_readings``, rattachable à tout type d'événement (même
patron que ``nav_event_engine_readings``), utilisée pour l'instant uniquement
au Cut-off comme nouvel ancrage de la chaîne ROB (R14/IR02).

Revision ID: 20260715_0106
Revises: 20260709_0105
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260715_0106"
down_revision = "20260709_0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nav_event_rob_by_fuel_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fuel_type", sa.String(length=20), nullable=False),
        sa.Column("rob_t", sa.Numeric(12, 3), nullable=True),
    )
    op.create_index("ix_nav_rob_by_fuel_event", "nav_event_rob_by_fuel_readings", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_nav_rob_by_fuel_event", table_name="nav_event_rob_by_fuel_readings")
    op.drop_table("nav_event_rob_by_fuel_readings")
