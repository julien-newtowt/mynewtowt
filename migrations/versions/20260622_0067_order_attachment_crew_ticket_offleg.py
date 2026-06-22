"""COM-04 PJ commande + CREW-04 embarquement hors leg + CREW-05 billet

Revision ID: 20260622_0067
Revises: 20260622_0066
Create Date: 2026-06-22 23:45:00

- COM-04 : pièce jointe (bon de commande / contrat) sur ``commercial_orders``.
- CREW-04 (A4) : ``crew_assignments.leg_id`` devient nullable + ajout de
  ``vessel_id`` (embarquement hors leg, rattaché au navire).
- CREW-05 : billet (titre de transport) attaché à l'embarquement.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0067"
down_revision: Union[str, None] = "20260622_0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # COM-04
    op.add_column("commercial_orders", sa.Column("attachment_path", sa.String(500), nullable=True))
    op.add_column("commercial_orders", sa.Column("attachment_filename", sa.String(255), nullable=True))
    op.add_column("commercial_orders", sa.Column("attachment_mime", sa.String(80), nullable=True))

    # CREW-04 — leg_id nullable + vessel_id
    with op.batch_alter_table("crew_assignments") as batch:
        batch.alter_column("leg_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("vessel_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_crew_assignments_vessel_id", "vessels", ["vessel_id"], ["id"]
        )
    op.create_index("ix_crew_assignments_vessel_id", "crew_assignments", ["vessel_id"])

    # CREW-05 — billet
    op.add_column("crew_assignments", sa.Column("ticket_path", sa.String(500), nullable=True))
    op.add_column("crew_assignments", sa.Column("ticket_filename", sa.String(255), nullable=True))
    op.add_column("crew_assignments", sa.Column("ticket_mime", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("crew_assignments", "ticket_mime")
    op.drop_column("crew_assignments", "ticket_filename")
    op.drop_column("crew_assignments", "ticket_path")
    op.drop_index("ix_crew_assignments_vessel_id", table_name="crew_assignments")
    with op.batch_alter_table("crew_assignments") as batch:
        batch.drop_constraint("fk_crew_assignments_vessel_id", type_="foreignkey")
        batch.drop_column("vessel_id")
        batch.alter_column("leg_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("commercial_orders", "attachment_mime")
    op.drop_column("commercial_orders", "attachment_filename")
    op.drop_column("commercial_orders", "attachment_path")
