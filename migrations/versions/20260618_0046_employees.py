"""Collaborateurs sédentaires — socle SIRH (lot L1).

Crée la table ``employees`` : dossier RH des salariés à terre. Aucune
donnée sensible (RIB, NIR, identité) — celles-ci restent dans Silae.
Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.1.

Revision ID: 20260618_0046
Revises: 20260618_0045
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260618_0046"
down_revision = "20260618_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "crew_member_id",
            sa.Integer(),
            sa.ForeignKey("crew_members.id"),
            nullable=True,
        ),
        sa.Column("matricule", sa.String(length=40), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email_pro", sa.String(length=255), nullable=True),
        sa.Column("phone_pro", sa.String(length=40), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("job_title", sa.String(length=150), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("manager_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("work_location", sa.String(length=100), nullable=True),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("exit_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("cp_balance", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("rtt_balance", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("silae_id", sa.String(length=60), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", name="uq_employees_user_id"),
        sa.UniqueConstraint("matricule", name="uq_employees_matricule"),
    )
    op.create_index("ix_employees_status", "employees", ["status"])
    op.create_index("ix_employees_department", "employees", ["department"])


def downgrade() -> None:
    op.drop_index("ix_employees_department", table_name="employees")
    op.drop_index("ix_employees_status", table_name="employees")
    op.drop_table("employees")
