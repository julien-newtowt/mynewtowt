"""MRV-04/05/07 — compteurs DO + ME/AE/ROB calculés + position DMS

Revision ID: 20260622_0063
Revises: 20260622_0062
Create Date: 2026-06-22 20:30:00

Reprise A1 hybride : réintroduit sur ``mrv_events`` les 4 compteurs DO, les
consommations ME/AE/total et le ROB calculé (chaînés par leg), la position en
DMS (exigée par l'export DNV Veracity) et l'auteur. Colonnes nullables →
migration additive et sûre.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0063"
down_revision: Union[str, None] = "20260622_0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [
    ("port_me_do_counter", sa.Numeric(12, 2)),
    ("stbd_me_do_counter", sa.Numeric(12, 2)),
    ("fwd_gen_do_counter", sa.Numeric(12, 2)),
    ("aft_gen_do_counter", sa.Numeric(12, 2)),
    ("bunkering_qty_t", sa.Numeric(10, 3)),
    ("me_consumption_t", sa.Numeric(10, 3)),
    ("ae_consumption_t", sa.Numeric(10, 3)),
    ("total_consumption_t", sa.Numeric(10, 3)),
    ("rob_calculated_t", sa.Numeric(10, 3)),
    ("lat_deg", sa.Integer()),
    ("lat_min", sa.Numeric(6, 3)),
    ("lat_ns", sa.CHAR(1)),
    ("lon_deg", sa.Integer()),
    ("lon_min", sa.Numeric(6, 3)),
    ("lon_ew", sa.CHAR(1)),
    ("created_by", sa.String(100)),
]


def upgrade() -> None:
    for name, type_ in _COLS:
        op.add_column("mrv_events", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in _COLS:
        op.drop_column("mrv_events", name)
