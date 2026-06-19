"""Éléments variables de paie (EVP) — lot L4 du SIRH.

Crée la table ``payroll_variables`` : collecte mensuelle des EVP par
collaborateur, verrouillage de période avant export Silae (L5).
Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.4.

Revision ID: 20260618_0049
Revises: 20260618_0048
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260618_0049"
down_revision = "20260618_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payroll_variables",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("evp_type", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("comment", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("absence_id", sa.Integer(), sa.ForeignKey("hr_absences.id"), nullable=True),
        sa.Column("export_batch_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_payroll_variables_employee_id", "payroll_variables", ["employee_id"])
    op.create_index("ix_payroll_variables_period", "payroll_variables", ["period"])
    op.create_index("ix_payroll_variables_status", "payroll_variables", ["status"])


def downgrade() -> None:
    op.drop_index("ix_payroll_variables_status", table_name="payroll_variables")
    op.drop_index("ix_payroll_variables_period", table_name="payroll_variables")
    op.drop_index("ix_payroll_variables_employee_id", table_name="payroll_variables")
    op.drop_table("payroll_variables")
