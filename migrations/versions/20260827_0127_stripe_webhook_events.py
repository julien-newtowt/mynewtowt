"""Idempotence des webhooks Stripe au niveau **événement**.

Jusqu'ici, la protection contre un rejeu reposait uniquement sur l'état métier
(``OnboardSale.cashbox_movement_id`` posé ⇒ ne pas re-encaisser). Deux limites :
la garde lit un état chargé **sans verrou**, donc deux livraisons concurrentes
du même événement pouvaient la franchir toutes les deux ; et elle ne couvre que
les types d'événements déjà branchés — tout futur type (remboursement, litige)
devrait réinventer sa protection.

Cette table enregistre l'``event.id`` **avant** tout traitement, sous contrainte
d'unicité : la seconde livraison échoue à l'insertion et repart en 200 sans
avoir rien touché. C'est la protection recommandée par Stripe, dont la
livraison est « au moins une fois ».

Purge : la table est un journal technique, sans valeur probante. Elle peut être
purgée par ancienneté (quelques semaines suffisent, la fenêtre de retry Stripe
étant de 3 jours) — contrairement aux registres de vente et de caisse.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0127"
down_revision = "20260827_0126"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("event_id", name="uq_stripe_webhook_events_event_id"),
    )
    op.create_index(
        "ix_stripe_webhook_events_received_at", "stripe_webhook_events", ["received_at"]
    )


def downgrade():
    op.drop_index("ix_stripe_webhook_events_received_at", table_name="stripe_webhook_events")
    op.drop_table("stripe_webhook_events")
