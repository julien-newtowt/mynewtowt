"""Fin opérationnelle du voyage : ``legs.voyage_completed_at`` (PLN-SEQ).

Un seul leg actif par navire : quand le leg suivant déclare son départ, le
leg précédent (arrivé, ATA posée) est terminé opérationnellement — le navire
a quitté le quai. La clôture administrative (``closure_*``) reste un workflow
distinct. Colonne nullable, aucune donnée existante modifiée ; la valeur est
posée uniquement par ``services.voyage_transitions.declare_departure``.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0137"
down_revision = "20260901_0136"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "legs", sa.Column("voyage_completed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("legs", "voyage_completed_at")
