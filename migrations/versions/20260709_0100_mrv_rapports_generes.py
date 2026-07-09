"""MRV lot 5 — rapports générés (Noon/Carbon/Stopover) et workflow de validation.

Crée la couche 3 (restitution) de l'architecture événementielle :
- ``env_reports`` — document généré depuis les événements (snapshot ``payload``
  JSON, workflow brouillon → attente_validation_master → valide_master →
  valide_siege) ;
- ``env_report_event_links`` — traçabilité rapport ↔ événements sources (PK
  composite) ;
- ``env_field_modifications`` — corrections post-validation tracées (R18),
  double FK nullable (rapport OU événement), taxonomie qualité 4 statuts.

Revision ID: 20260709_0100
Revises: 20260709_0099
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260709_0100"
down_revision = "20260709_0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "env_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_type", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="brouillon"
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validated_master_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "validated_master_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("validated_siege_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "validated_siege_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_env_reports_leg", "env_reports", ["leg_id"])
    op.create_index("ix_env_reports_type", "env_reports", ["report_type"])
    op.create_index("ix_env_reports_status", "env_reports", ["status"])

    op.create_table(
        "env_report_event_links",
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("env_reports.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_env_report_links_event", "env_report_event_links", ["event_id"])

    op.create_table(
        "env_field_modifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("env_reports.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column("initial_value", sa.Text(), nullable=True),
        sa.Column("corrected_value", sa.Text(), nullable=True),
        sa.Column("justification_text", sa.Text(), nullable=False),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "timestamp_utc",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resulting_quality_status", sa.String(length=20), nullable=False),
    )
    op.create_index("ix_env_field_mods_report", "env_field_modifications", ["report_id"])
    op.create_index("ix_env_field_mods_event", "env_field_modifications", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_env_field_mods_event", table_name="env_field_modifications")
    op.drop_index("ix_env_field_mods_report", table_name="env_field_modifications")
    op.drop_table("env_field_modifications")

    op.drop_index("ix_env_report_links_event", table_name="env_report_event_links")
    op.drop_table("env_report_event_links")

    op.drop_index("ix_env_reports_status", table_name="env_reports")
    op.drop_index("ix_env_reports_type", table_name="env_reports")
    op.drop_index("ix_env_reports_leg", table_name="env_reports")
    op.drop_table("env_reports")
