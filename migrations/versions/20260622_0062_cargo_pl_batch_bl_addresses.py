"""CARGO-01/02 — adresses BL + marchandise + numéro de BL sur les batches

Revision ID: 20260622_0062
Revises: 20260622_0061
Create Date: 2026-06-22 19:45:00

Reprise du module Cargo : réintroduit sur ``packing_list_batches`` les parties
du connaissement (shipper/notify/consignee), la description marchandise et la
numérotation persistante du Bill of Lading (TUAW_{leg_code}_{seq:03d}).
Toutes les colonnes sont nullables → migration additive et sûre.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0062"
down_revision: Union[str, None] = "20260622_0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADDRESS_COLS = []
for party in ("shipper", "notify", "consignee"):
    _ADDRESS_COLS += [
        (f"{party}_name", sa.String(200)),
        (f"{party}_address", sa.Text()),
        (f"{party}_postal", sa.String(20)),
        (f"{party}_city", sa.String(100)),
        (f"{party}_country", sa.String(100)),
    ]

_OTHER_COLS = [
    ("type_of_goods", sa.String(200)),
    ("description_of_goods", sa.Text()),
    ("bl_number", sa.String(50)),
    ("bl_issued_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    for name, type_ in _ADDRESS_COLS + _OTHER_COLS:
        op.add_column("packing_list_batches", sa.Column(name, type_, nullable=True))
    # Unique : interdit deux Bills of Lading au même numéro (les NULL restent
    # multiples — batches sans BL émis). Anti-doublon au niveau base.
    op.create_index(
        "ix_packing_list_batches_bl_number",
        "packing_list_batches",
        ["bl_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_packing_list_batches_bl_number", table_name="packing_list_batches")
    for name, _type in _ADDRESS_COLS + _OTHER_COLS:
        op.drop_column("packing_list_batches", name)
