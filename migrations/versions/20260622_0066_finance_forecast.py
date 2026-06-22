"""FIN-01 — suivi prévisionnel/réalisé par poste sur LegFinance (A2)

Revision ID: 20260622_0066
Revises: 20260622_0065
Create Date: 2026-06-22 23:00:00

Reprise V2 (contrôle de gestion) : réintroduit le budget prévisionnel par
poste à côté du réel consolidé par le rollup. Colonnes NOT NULL avec défaut 0
→ migration additive et sûre (les legs existants démarrent à 0 de budget).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0066"
down_revision: Union[str, None] = "20260622_0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FORECAST_COLS = (
    "revenue_forecast_eur",
    "port_fees_forecast_eur",
    "docker_costs_forecast_eur",
    "opex_share_forecast_eur",
    "other_costs_forecast_eur",
    "margin_forecast_eur",
)


def upgrade() -> None:
    for name in _FORECAST_COLS:
        op.add_column(
            "leg_finances",
            sa.Column(name, sa.Numeric(12, 2), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for name in _FORECAST_COLS:
        op.drop_column("leg_finances", name)
