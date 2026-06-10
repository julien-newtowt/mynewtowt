"""Leg.distance_nm — distance orthodromique POL→POD persistée.

Alimente le label Anemos (CO₂ évité) avec la distance réelle du leg
plutôt qu'une table de paires de ports en dur. Backfill par haversine
calculé directement en SQL depuis les coordonnées des ports.

Revision ID: 20260519_0014
Revises: 20260519_0013
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0014"
down_revision = "20260519_0013"
branch_labels = None
depends_on = None


# Rayon terrestre en milles nautiques (cf. services/ports.py).
_EARTH_RADIUS_NM = 3440.065

_BACKFILL_SQL = f"""
UPDATE legs SET distance_nm = ROUND(sub.dist::numeric, 2)
FROM (
    SELECT l.id AS leg_id,
        2 * {_EARTH_RADIUS_NM} * asin(sqrt(
            power(sin(radians(pod.latitude - pol.latitude) / 2), 2)
            + cos(radians(pol.latitude)) * cos(radians(pod.latitude))
            * power(sin(radians(pod.longitude - pol.longitude) / 2), 2)
        )) AS dist
    FROM legs l
    JOIN ports pol ON pol.id = l.departure_port_id
    JOIN ports pod ON pod.id = l.arrival_port_id
    WHERE pol.latitude IS NOT NULL AND pol.longitude IS NOT NULL
      AND pod.latitude IS NOT NULL AND pod.longitude IS NOT NULL
) AS sub
WHERE legs.id = sub.leg_id;
"""


def upgrade() -> None:
    op.add_column(
        "legs",
        sa.Column("distance_nm", sa.Numeric(8, 2), nullable=True),
    )
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("legs", "distance_nm")
