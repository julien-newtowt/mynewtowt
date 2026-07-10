"""MRV — socle du moteur de règles de validation (LOT 2).

Crée la couche Référentiel du moteur de règles :
- ``validation_rules`` (catalogue des 31 règles R01-R26 + IR01-IR05) ;
- ``validation_rule_thresholds`` (seuils paramétrables, override par navire) ;
- ``dashboard_parameters`` (paramètres du dashboard Performance Env.) ;
- ``quality_check_results`` (journal d'anomalies, référence polymorphe
  ``subject_type``/``subject_id`` sans FK — cf. modèle).

Seed vessel-indépendant (donc sûr) : 31 règles + 20 seuils globaux
(16 Matrice §6 + 2 densité R16 + 2 bornes plausibles R11) + 4 paramètres
dashboard. Les valeurs et le catalogue sont importés depuis
``app.services.validation_engine`` (source de vérité unique, également
utilisée par le seed idempotent du boot dev et l'action d'init admin).

Revision ID: 20260709_0097
Revises: 20260709_0096
Create Date: 2026-07-09 00:00:00.000000
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "20260709_0097"
down_revision = "20260709_0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_rules",
        sa.Column("rule_id", sa.String(length=8), primary_key=True),
        sa.Column("domain", sa.String(length=60), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "default_severity",
            sa.String(length=12),
            nullable=False,
            server_default="warning",
        ),
        sa.Column("scope", sa.String(length=12), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "validation_rule_thresholds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id",
            sa.String(length=8),
            sa.ForeignKey("validation_rules.rule_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("parameter_name", sa.String(length=80), nullable=False),
        sa.Column("value", sa.Numeric(15, 6), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "rule_id", "vessel_id", "parameter_name", name="uq_vrt_rule_vessel_param"
        ),
    )
    op.create_index("ix_vrt_rule", "validation_rule_thresholds", ["rule_id"])
    op.create_index("ix_vrt_vessel", "validation_rule_thresholds", ["vessel_id"])

    op.create_table(
        "dashboard_parameters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parameter_name", sa.String(length=80), nullable=False),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("value", sa.Numeric(15, 6), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "parameter_name", "vessel_id", name="uq_dashparam_name_vessel"
        ),
    )
    op.create_index("ix_dashparam_vessel", "dashboard_parameters", ["vessel_id"])

    op.create_table(
        "quality_check_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id",
            sa.String(length=8),
            sa.ForeignKey("validation_rules.rule_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("result", sa.String(length=8), nullable=False),
        sa.Column("severity_applied", sa.String(length=12), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_qcr_rule_executed", "quality_check_results", ["rule_id", "executed_at"]
    )
    op.create_index(
        "ix_qcr_subject", "quality_check_results", ["subject_type", "subject_id"]
    )
    op.create_index("ix_qcr_leg", "quality_check_results", ["leg_id"])
    op.create_index("ix_qcr_run", "quality_check_results", ["run_id"])

    _seed()


def _seed() -> None:
    """Seed du catalogue (source de vérité : app.services.validation_engine)."""
    from app.services.validation_engine import (
        DASHBOARD_SEED,
        RULE_SEED,
        THRESHOLD_SEED,
    )

    rules_tbl = sa.table(
        "validation_rules",
        sa.column("rule_id", sa.String),
        sa.column("domain", sa.String),
        sa.column("description", sa.Text),
        sa.column("default_severity", sa.String),
        sa.column("scope", sa.String),
        sa.column("active", sa.Boolean),
    )
    op.bulk_insert(
        rules_tbl,
        [
            {
                "rule_id": rid,
                "domain": domain,
                "description": desc,
                "default_severity": severity,
                "scope": scope,
                "active": active,
            }
            for (rid, domain, desc, severity, scope, active) in RULE_SEED
        ],
    )

    thr_tbl = sa.table(
        "validation_rule_thresholds",
        sa.column("rule_id", sa.String),
        sa.column("vessel_id", sa.Integer),
        sa.column("parameter_name", sa.String),
        sa.column("value", sa.Numeric),
        sa.column("unit", sa.String),
        sa.column("provisional", sa.Boolean),
        sa.column("note", sa.Text),
    )
    op.bulk_insert(
        thr_tbl,
        [
            {
                "rule_id": rid,
                "vessel_id": None,
                "parameter_name": param,
                "value": Decimal(value),
                "unit": unit,
                "provisional": provisional,
                "note": note,
            }
            for (rid, param, value, unit, provisional, note) in THRESHOLD_SEED
        ],
    )

    dash_tbl = sa.table(
        "dashboard_parameters",
        sa.column("parameter_name", sa.String),
        sa.column("vessel_id", sa.Integer),
        sa.column("value", sa.Numeric),
        sa.column("unit", sa.String),
    )
    op.bulk_insert(
        dash_tbl,
        [
            {
                "parameter_name": param,
                "vessel_id": None,
                "value": Decimal(value),
                "unit": unit,
            }
            for (param, value, unit) in DASHBOARD_SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("quality_check_results")
    op.drop_table("dashboard_parameters")
    op.drop_table("validation_rule_thresholds")
    op.drop_table("validation_rules")
