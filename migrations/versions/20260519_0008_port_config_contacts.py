"""PortConfig enrichi — contacts agent/pilote, docs requis, restrictions.

Audit Persona 3 (commandant) : "écran prochaine escale absent → port
details cachées". On ajoute les champs nécessaires sur ``port_configs``
pour que ``/captain/next-port`` affiche agent, pilote VHF, docs requis.

Revision ID: 20260519_0008
Revises: 20260519_0007
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0008"
down_revision = "20260519_0007"
branch_labels = None
depends_on = None


_NEW_COLS = [
    ("agent_name", sa.String(200)),
    ("agent_phone", sa.String(40)),
    ("agent_email", sa.String(200)),
    ("pilot_vhf_channel", sa.String(10)),
    ("pilot_phone", sa.String(40)),
    ("port_control_vhf_channel", sa.String(10)),
    ("documents_required", sa.Text()),
    ("restrictions", sa.Text()),
    ("notes_for_captain", sa.Text()),
]


def upgrade() -> None:
    with op.batch_alter_table("port_configs") as batch:
        for name, type_ in _NEW_COLS:
            batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("port_configs") as batch:
        for name, _ in reversed(_NEW_COLS):
            batch.drop_column(name)
