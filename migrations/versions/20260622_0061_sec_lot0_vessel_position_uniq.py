"""SEC-04 — unicité + index (vessel_id, recorded_at) sur vessel_positions

Revision ID: 20260622_0061
Revises: 20260622_0060
Create Date: 2026-06-22 19:30:00

Lot 0 (sécurité/intégrité). Garantit l'idempotence de l'upload satcom (anti-
doublon en concurrence) et indexe les lectures historiques (navire × période).
Avant d'ajouter la contrainte, on supprime les éventuels doublons hérités
(on garde la ligne d'``id`` minimal par couple navire/instant).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0061"
down_revision: Union[str, None] = "20260622_0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dédoublonnage défensif avant pose de la contrainte unique.
    op.execute(
        """
        DELETE FROM vessel_positions a
        USING vessel_positions b
        WHERE a.vessel_id = b.vessel_id
          AND a.recorded_at = b.recorded_at
          AND a.id > b.id
        """
    )
    op.create_unique_constraint(
        "uq_vessel_position_time", "vessel_positions", ["vessel_id", "recorded_at"]
    )
    op.create_index(
        "ix_vessel_positions_vessel_time",
        "vessel_positions",
        ["vessel_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_vessel_positions_vessel_time", table_name="vessel_positions")
    op.drop_constraint("uq_vessel_position_time", "vessel_positions", type_="unique")
