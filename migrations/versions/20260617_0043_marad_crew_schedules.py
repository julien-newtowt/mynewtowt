"""Marad (read-only) : miroir des plannings d'embarquement (CrewingSchedule).

Table ``marad_crew_schedules`` — import LECTURE SEULE des schedules Marad.
Chez Marad, un « voyage » correspond à notre ``leg`` : on conserve la référence
voyage et on réconcilie au besoin avec un ``leg`` interne via ``leg_code``.
cf. docs/integrations/marad-crew-readonly.md §3.3.

Revision ID: 20260617_0043
Revises: 20260617_0042
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260617_0043"
down_revision = "20260617_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marad_crew_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("marad_schedule_id", sa.String(length=36), nullable=False),
        sa.Column(
            "crew_member_id",
            sa.Integer(),
            sa.ForeignKey("crew_members.id"),
            nullable=True,
        ),
        sa.Column("marad_crew_id", sa.String(length=36), nullable=True),
        sa.Column("vessel_id", sa.Integer(), sa.ForeignKey("vessels.id"), nullable=True),
        sa.Column("marad_vessel_name", sa.String(length=120), nullable=True),
        sa.Column("marad_voyage_ref", sa.String(length=80), nullable=True),
        sa.Column("leg_id", sa.Integer(), sa.ForeignKey("legs.id"), nullable=True),
        sa.Column("rank_label", sa.String(length=80), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_marad_crew_schedules_marad_schedule_id",
        "marad_crew_schedules",
        ["marad_schedule_id"],
        unique=True,
    )
    op.create_index(
        "ix_marad_crew_schedules_crew_member_id",
        "marad_crew_schedules",
        ["crew_member_id"],
    )
    op.create_index(
        "ix_marad_crew_schedules_marad_crew_id",
        "marad_crew_schedules",
        ["marad_crew_id"],
    )
    op.create_index(
        "ix_marad_crew_schedules_marad_voyage_ref",
        "marad_crew_schedules",
        ["marad_voyage_ref"],
    )
    op.create_index(
        "ix_marad_crew_schedules_leg_id",
        "marad_crew_schedules",
        ["leg_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_marad_crew_schedules_leg_id", table_name="marad_crew_schedules")
    op.drop_index("ix_marad_crew_schedules_marad_voyage_ref", table_name="marad_crew_schedules")
    op.drop_index("ix_marad_crew_schedules_marad_crew_id", table_name="marad_crew_schedules")
    op.drop_index("ix_marad_crew_schedules_crew_member_id", table_name="marad_crew_schedules")
    op.drop_index("ix_marad_crew_schedules_marad_schedule_id", table_name="marad_crew_schedules")
    op.drop_table("marad_crew_schedules")
