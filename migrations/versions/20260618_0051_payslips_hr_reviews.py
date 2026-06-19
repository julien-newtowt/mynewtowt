"""Coffre-fort bulletins + entretiens — lot L6 du SIRH.

Crée ``payslips`` (bulletins de paie archivés, contenu binaire en base) et
``hr_reviews`` (entretiens annuels/professionnels + échéances). Voir
``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.5 / §4.6.

Revision ID: 20260618_0051
Revises: 20260618_0050
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260618_0051"
down_revision = "20260618_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payslips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_payslips_employee_id", "payslips", ["employee_id"])
    op.create_index("ix_payslips_period", "payslips", ["period"])

    op.create_table(
        "hr_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("review_type", sa.String(length=20), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("next_due_date", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_hr_reviews_employee_id", "hr_reviews", ["employee_id"])
    op.create_index("ix_hr_reviews_next_due", "hr_reviews", ["next_due_date"])


def downgrade() -> None:
    op.drop_index("ix_hr_reviews_next_due", table_name="hr_reviews")
    op.drop_index("ix_hr_reviews_employee_id", table_name="hr_reviews")
    op.drop_table("hr_reviews")
    op.drop_index("ix_payslips_period", table_name="payslips")
    op.drop_index("ix_payslips_employee_id", table_name="payslips")
    op.drop_table("payslips")
