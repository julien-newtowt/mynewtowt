"""planning scenarios (provisional planning) tables

Revision ID: 20260622_0055
Revises: 20260619_0054
Create Date: 2026-06-22 09:00:00

Tables isolées de ``legs`` : une planification provisoire (scénario what-if)
n'impacte jamais la planification en cours.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0055"
down_revision: Union[str, None] = "20260619_0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "planning_scenarios",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_by_name", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "scenario_legs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "scenario_id",
            sa.Integer,
            sa.ForeignKey("planning_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vessel_id", sa.Integer, sa.ForeignKey("vessels.id"), nullable=False),
        sa.Column("departure_port_id", sa.Integer, sa.ForeignKey("ports.id"), nullable=False),
        sa.Column("arrival_port_id", sa.Integer, sa.ForeignKey("ports.id"), nullable=False),
        sa.Column("etd", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label", sa.String(40)),
        sa.Column("status", sa.String(20), server_default="planned", nullable=False),
        sa.Column("port_stay_planned_hours", sa.Integer),
        sa.Column("transit_speed_kn", sa.Float),
        sa.Column("elongation_coef", sa.Float),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scenario_legs_scenario_id", "scenario_legs", ["scenario_id"])
    op.create_index("ix_scenario_legs_etd", "scenario_legs", ["etd"])


def downgrade() -> None:
    op.drop_index("ix_scenario_legs_etd", table_name="scenario_legs")
    op.drop_index("ix_scenario_legs_scenario_id", table_name="scenario_legs")
    op.drop_table("scenario_legs")
    op.drop_table("planning_scenarios")
