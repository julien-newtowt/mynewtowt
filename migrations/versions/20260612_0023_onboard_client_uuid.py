"""onboard PWA offline — client_uuid (dédoublonnage) sur noon_reports + watch_logs

Revision ID: 20260612_0023
Revises: 20260602_0022
Create Date: 2026-06-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260612_0023"
down_revision = "20260602_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("noon_reports", sa.Column("client_uuid", sa.String(36), nullable=True))
    op.create_unique_constraint("uq_noon_reports_client_uuid", "noon_reports", ["client_uuid"])
    op.add_column("watch_logs", sa.Column("client_uuid", sa.String(36), nullable=True))
    op.create_unique_constraint("uq_watch_logs_client_uuid", "watch_logs", ["client_uuid"])


def downgrade() -> None:
    op.drop_constraint("uq_watch_logs_client_uuid", "watch_logs", type_="unique")
    op.drop_column("watch_logs", "client_uuid")
    op.drop_constraint("uq_noon_reports_client_uuid", "noon_reports", type_="unique")
    op.drop_column("noon_reports", "client_uuid")
