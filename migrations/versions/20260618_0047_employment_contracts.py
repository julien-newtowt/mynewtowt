"""Contrats & avenants des collaborateurs sédentaires (lot L2).

Crée la table ``employment_contracts`` : contrats de travail (CDI/CDD,
alternance, stage) et avenants, avec échéances (période d'essai, terme).
Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.2.

Revision ID: 20260618_0047
Revises: 20260618_0046
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260618_0047"
down_revision = "20260618_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employment_contracts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            nullable=False,
        ),
        sa.Column("contract_type", sa.String(length=30), nullable=False),
        sa.Column(
            "parent_contract_id",
            sa.Integer(),
            sa.ForeignKey("employment_contracts.id"),
            nullable=True,
        ),
        sa.Column("is_amendment", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "convention",
            sa.String(length=60),
            nullable=False,
            server_default="transport_maritime",
        ),
        sa.Column("classification", sa.String(length=80), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("trial_end_date", sa.Date(), nullable=True),
        sa.Column("weekly_hours", sa.Numeric(5, 2), nullable=True),
        sa.Column("gross_monthly", sa.Numeric(10, 2), nullable=True),
        sa.Column("motive", sa.String(length=255), nullable=True),
        sa.Column("document_path", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
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
    )
    op.create_index(
        "ix_employment_contracts_employee_id", "employment_contracts", ["employee_id"]
    )
    op.create_index("ix_employment_contracts_status", "employment_contracts", ["status"])
    op.create_index("ix_employment_contracts_end_date", "employment_contracts", ["end_date"])


def downgrade() -> None:
    op.drop_index("ix_employment_contracts_end_date", table_name="employment_contracts")
    op.drop_index("ix_employment_contracts_status", table_name="employment_contracts")
    op.drop_index("ix_employment_contracts_employee_id", table_name="employment_contracts")
    op.drop_table("employment_contracts")
