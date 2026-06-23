"""COM-02 — champs riches de la commande + lien grille + route/livraison

Revision ID: 20260622_0064
Revises: 20260622_0063
Create Date: 2026-06-22 21:30:00

Reprise du module Commercial : réintroduit sur ``commercial_orders`` les
caractéristiques perdues en V3 (format/poids palette, THC, frais booking &
documentaires), la route souhaitée (POL/POD locodes) et la fenêtre de
livraison — qui pilotent l'affectation au leg (COM-01) — ainsi que le lien
vers la grille appliquée (``rate_grid_id`` / ``rate_grid_line_id``), pour
tracer la grille et afficher les « commandes liées » sur la fiche grille.
Toutes les colonnes sont nullables (ou avec défaut) → migration additive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0064"
down_revision: Union[str, None] = "20260622_0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [
    ("palette_format", sa.String(20)),
    ("weight_per_palette_kg", sa.Numeric(8, 2)),
    ("booking_fee", sa.Numeric(10, 2)),
    ("documentation_fee", sa.Numeric(10, 2)),
    ("departure_locode", sa.String(5)),
    ("arrival_locode", sa.String(5)),
    ("delivery_date_start", sa.Date()),
    ("delivery_date_end", sa.Date()),
    ("rate_grid_id", sa.Integer()),
    ("rate_grid_line_id", sa.Integer()),
]


def upgrade() -> None:
    for name, type_ in _COLS:
        op.add_column("commercial_orders", sa.Column(name, type_, nullable=True))
    op.add_column(
        "commercial_orders",
        sa.Column("thc_included", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_commercial_orders_rate_grid_id", "commercial_orders", ["rate_grid_id"]
    )
    op.create_foreign_key(
        "fk_commercial_orders_rate_grid_id",
        "commercial_orders",
        "rate_grids",
        ["rate_grid_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_commercial_orders_rate_grid_line_id",
        "commercial_orders",
        "rate_grid_lines",
        ["rate_grid_line_id"],
        ["id"],
    )
    # COM-01 — anti-doublon : une commande au plus une fois par leg.
    op.create_unique_constraint(
        "uq_order_assignment_order_leg", "order_assignments", ["order_id", "leg_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_order_assignment_order_leg", "order_assignments", type_="unique"
    )
    op.drop_constraint(
        "fk_commercial_orders_rate_grid_line_id", "commercial_orders", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_commercial_orders_rate_grid_id", "commercial_orders", type_="foreignkey"
    )
    op.drop_index("ix_commercial_orders_rate_grid_id", table_name="commercial_orders")
    for name, _type in _COLS:
        op.drop_column("commercial_orders", name)
    op.drop_column("commercial_orders", "thc_included")
