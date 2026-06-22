"""noon report hold conditions (temperature & humidity)

Revision ID: 20260622_0057
Revises: 20260622_0056
Create Date: 2026-06-22 12:00:00

Section « Hold conditions » du formulaire officiel CFOTE_05 : température
(°C) et humidité relative (%) par cale, relevées à minuit et à midi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0057"
down_revision: Union[str, None] = "20260622_0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "noon_report_holds",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "noon_report_id",
            sa.Integer,
            sa.ForeignKey("noon_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("location", sa.String(40), nullable=False),
        sa.Column("temp_midnight_c", sa.Float),
        sa.Column("humidity_midnight_pct", sa.Float),
        sa.Column("temp_midday_c", sa.Float),
        sa.Column("humidity_midday_pct", sa.Float),
    )
    op.create_index("ix_noon_report_holds_noon_report_id", "noon_report_holds", ["noon_report_id"])


def downgrade() -> None:
    op.drop_index("ix_noon_report_holds_noon_report_id", table_name="noon_report_holds")
    op.drop_table("noon_report_holds")
