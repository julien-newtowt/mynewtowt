"""Historique append-only des offres commerciales (chaînage SHA-256).

Lot 3 de la refonte commerciale. Une offre engage un prix : en cas de
contestation il faut pouvoir dire **ce que valait l'offre à chaque instant**, et
montrer que l'historique n'a pas été retouché.

``activity_logs`` ne suffit pas pour cela : il consigne qu'une action a eu lieu,
avec un détail en texte libre, sans ancienne ni nouvelle valeur — et il est
purgeable par ancienneté. Cette table conserve, pour chaque révision, le diff
champ par champ, l'état complet de l'offre, et un hachage chaîné sur la
révision précédente (retirer ou modifier une ligne casse la chaîne des
suivantes, donc la falsification devient détectable).

Aucune donnée à reprendre : les offres déjà en base n'ont pas d'historique
antérieur, et en fabriquer un rétroactivement reviendrait à inventer des
révisions qui n'ont jamais eu lieu. Leur première révision sera enregistrée à
leur prochaine modification.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260826_0121"
down_revision = "20260826_0120"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rate_offer_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(length=200), nullable=True),
        sa.Column("actor_role", sa.String(length=40), nullable=True),
        sa.Column("changes_json", sa.Text(), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["offer_id"], ["rate_offers.id"], ondelete="CASCADE"),
        # Deux révisions ne peuvent pas revendiquer la même place dans la chaîne.
        sa.UniqueConstraint("offer_id", "sequence", name="uq_rate_offer_revision_sequence"),
    )
    op.create_index("ix_rate_offer_revisions_offer_id", "rate_offer_revisions", ["offer_id"])
    op.create_index("ix_rate_offer_revisions_at", "rate_offer_revisions", ["at"])


def downgrade():
    op.drop_index("ix_rate_offer_revisions_at", table_name="rate_offer_revisions")
    op.drop_index("ix_rate_offer_revisions_offer_id", table_name="rate_offer_revisions")
    op.drop_table("rate_offer_revisions")
