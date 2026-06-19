"""Congés & absences des collaborateurs sédentaires (lot L3).

Crée la table ``hr_absences`` : cycle de demande/validation des congés des
sédentaires, distinct de ``crew_leaves`` (marins). Décompte en jours
ouvrés. Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.3.

Revision ID: 20260618_0048
Revises: 20260618_0047
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260618_0048"
down_revision = "20260618_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hr_absences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("half_day_start", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("half_day_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("business_days", sa.Numeric(5, 1), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="requested"),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("silae_exported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_hr_absences_employee_id", "hr_absences", ["employee_id"])
    op.create_index("ix_hr_absences_status", "hr_absences", ["status"])
    op.create_index("ix_hr_absences_dates", "hr_absences", ["start_date", "end_date"])


def downgrade() -> None:
    op.drop_index("ix_hr_absences_dates", table_name="hr_absences")
    op.drop_index("ix_hr_absences_status", table_name="hr_absences")
    op.drop_index("ix_hr_absences_employee_id", table_name="hr_absences")
    op.drop_table("hr_absences")
