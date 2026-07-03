"""Durcissement des règles de planification (audit 2026-07).

1. **``schedule_revisions``** — historique append-only des recalculs de
   dates (édition planning, drag-drop Gantt, ETA-shift capitaine, cascade).
   Survit à la suppression du leg (FK ``SET NULL`` + snapshot ``leg_code``).

2. **Normalisation des statuts** — la taxonomie canonique est
   ``in_progress`` ; les graphies historiques ``inprogress`` (écrites par
   d'anciens flux et testées par les templates) sont converties sur
   ``legs`` et ``scenario_legs``.

3. **Contrainte d'exclusion Postgres** (filet anti-course) : deux legs non
   annulés d'un même navire ne peuvent pas se chevaucher dans le temps —
   ``EXCLUDE USING gist (vessel_id WITH =, tstzrange(etd, eta) WITH &&)``.
   ``DEFERRABLE INITIALLY DEFERRED`` : la cascade décale plusieurs legs
   dans une même transaction, la contrainte est vérifiée au commit.
   Postgres uniquement (les tests SQLite s'appuient sur la validation
   applicative). ⚠ Si des chevauchements existent déjà en base, la
   migration échoue : les résoudre d'abord (requête dans la docstring du
   service ``validate_leg_schedule``).

Revision ID: 20260703_0094
Revises: 20260702_0093
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260703_0094"
down_revision = "20260702_0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Historique des recalculs ────────────────────────────────────
    op.create_table(
        "schedule_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("leg_code", sa.String(length=20), nullable=True),
        sa.Column("vessel_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column(
            "trigger_leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("old_etd", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_etd", sa.DateTime(timezone=True), nullable=True),
        sa.Column("old_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_schedule_revisions_leg_id", "schedule_revisions", ["leg_id"])
    op.create_index("ix_schedule_revisions_vessel_id", "schedule_revisions", ["vessel_id"])
    op.create_index("ix_schedule_revisions_batch_id", "schedule_revisions", ["batch_id"])
    op.create_index("ix_schedule_revisions_created", "schedule_revisions", ["created_at"])

    # ── 2. Normalisation des statuts de legs ──────────────────────────
    op.execute("UPDATE legs SET status = 'in_progress' WHERE status = 'inprogress'")
    op.execute("UPDATE scenario_legs SET status = 'in_progress' WHERE status = 'inprogress'")

    # ── 3. Contrainte d'exclusion anti-chevauchement (Postgres) ────────
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        op.execute(
            """
            ALTER TABLE legs ADD CONSTRAINT legs_no_vessel_overlap
            EXCLUDE USING gist (
                vessel_id WITH =,
                tstzrange(etd, eta) WITH &&
            )
            WHERE (status <> 'cancelled')
            DEFERRABLE INITIALLY DEFERRED
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE legs DROP CONSTRAINT IF EXISTS legs_no_vessel_overlap")
    op.execute("UPDATE legs SET status = 'inprogress' WHERE status = 'in_progress'")
    op.execute("UPDATE scenario_legs SET status = 'inprogress' WHERE status = 'in_progress'")
    op.drop_index("ix_schedule_revisions_created", table_name="schedule_revisions")
    op.drop_index("ix_schedule_revisions_batch_id", table_name="schedule_revisions")
    op.drop_index("ix_schedule_revisions_vessel_id", table_name="schedule_revisions")
    op.drop_index("ix_schedule_revisions_leg_id", table_name="schedule_revisions")
    op.drop_table("schedule_revisions")
