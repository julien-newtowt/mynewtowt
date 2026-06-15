"""KPI : indicateurs carbone auto-calculés (Carbon Report CFOTE_09) + verrou manuel.

Ajoute à ``leg_kpis`` la consommation DO, le CO₂ émis et les intensités
(par mille / tonne / tonne·mille), plus un drapeau ``is_manual`` qui protège
un KPI saisi à la main contre l'auto-alimentation.

Revision ID: 20260615_0039
Revises: 20260615_0038
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260615_0039"
down_revision = "20260615_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leg_kpis", sa.Column("do_consumed_t", sa.Numeric(12, 3), nullable=True))
    op.add_column("leg_kpis", sa.Column("co2_emitted_kg", sa.Numeric(14, 2), nullable=True))
    op.add_column("leg_kpis", sa.Column("co2_per_nm_kg", sa.Numeric(12, 3), nullable=True))
    op.add_column("leg_kpis", sa.Column("co2_per_t_kg", sa.Numeric(12, 3), nullable=True))
    op.add_column("leg_kpis", sa.Column("co2_per_tnm_g", sa.Numeric(12, 3), nullable=True))
    op.add_column(
        "leg_kpis",
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    for name in (
        "is_manual",
        "co2_per_tnm_g",
        "co2_per_t_kg",
        "co2_per_nm_kg",
        "co2_emitted_kg",
        "do_consumed_t",
    ):
        op.drop_column("leg_kpis", name)
