"""drop passenger module tables (v3.0.0 NEWTOWT post-restructuring)

Revision ID: 0002_drop_passengers
Revises: 0001_baseline_post_sprint2
Create Date: 2026-05-07 00:00:00

Suite à la restructuration juridique (NEWTOWT post-TOWT liquidation),
l'activité passager n'est plus opérée. Cette migration supprime les
tables et colonnes liées.

⚠️  IRRÉVERSIBLE — ne pas appliquer sans backup préalable.

Le SQL équivalent est aussi disponible en standalone dans
``migrations/0003_drop_passengers.sql`` pour les ops qui préfèrent
exécuter via ``psql`` directement.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0002_drop_passengers"
down_revision: Union[str, None] = "0001_baseline_post_sprint2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop FK columns on related tables
    op.execute("ALTER TABLE IF EXISTS claims              DROP COLUMN IF EXISTS passenger_id")
    op.execute("ALTER TABLE IF EXISTS notifications       DROP COLUMN IF EXISTS booking_id")
    op.execute("ALTER TABLE IF EXISTS portal_messages     DROP COLUMN IF EXISTS booking_id")
    op.execute("ALTER TABLE IF EXISTS portal_access_logs  DROP COLUMN IF EXISTS booking_id")

    # 2. Drop passenger SOF event types
    op.execute(
        "DELETE FROM sof_events WHERE event_type IN "
        "('PAX_EMBARK', 'PAX_DISEMBARK', 'PAX_SAFETY_DRILL', 'PAX_MUSTER')"
    )

    # 3. Drop passenger notifications (legacy types)
    op.execute(
        "DELETE FROM notifications WHERE type IN "
        "('new_passenger_message', 'new_passenger_booking')"
    )

    # 4. Drop the tables themselves (CASCADE pour les FK reverse)
    for tbl in (
        "satisfaction_responses",
        "preboarding_forms",
        "passenger_audit_logs",
        "passenger_documents",
        "passenger_payments",
        "passengers",
        "passenger_bookings",
        "cabin_price_grid",
    ):
        op.execute(f'DROP TABLE IF EXISTS {tbl} CASCADE')


def downgrade() -> None:
    # No-op: the passenger module has been removed at the application
    # level. Recreating the tables without the ORM models would yield
    # an unusable schema. Restore from backup if rollback is needed.
    raise NotImplementedError(
        "Downgrade not supported. Restore from backup taken before applying "
        "0002_drop_passengers if rollback is required."
    )
