"""Variables CO₂ versionnées + overrides matrice RBAC.

- co2_variables (ENV-02) : versionnage append-only des facteurs
  d'émission — /admin/co2 insère une nouvelle ligne (is_current=True)
  et bascule la précédente à is_current=False ; l'historique est
  conservé.
- role_permissions (ARC-04) : overrides cellule par cellule de la
  grille rôles × modules — le défaut reste app.permissions._MATRIX,
  seules les cellules différentes du défaut sont stockées.

Revision ID: 20260612_0026
Revises: 20260612_0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0026"
down_revision = "20260612_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- co2_variables ------------------------------------------------------
    op.create_table(
        "co2_variables",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Numeric(12, 6), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_co2_variables_name", "co2_variables", ["name"])

    # --- role_permissions ---------------------------------------------------
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=3), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.UniqueConstraint("role", "module", name="uq_role_permissions_role_module"),
    )


def downgrade() -> None:
    op.drop_table("role_permissions")
    op.drop_index("ix_co2_variables_name", table_name="co2_variables")
    op.drop_table("co2_variables")
