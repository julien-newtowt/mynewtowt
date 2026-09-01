"""Registre de remise des originaux du connaissement — §5.1.

Cf. ``docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md`` §5.1 :

    « L'idéal serait de tracker le timestamp de cette action ou ajouter une case de
    confirmation de réception côté client. Cette case devrait aussi apparaître pour
    l'équipe opérations, en mode backup. Si les BLs sont envoyés en papier par
    exemple, l'équipe opérations pourra confirmer la réception côté client en
    ajoutant la date et heure de confirmation et moyen (téléphone, mail, etc.) +
    PJ possible. »

C'est **exactement** le dispositif dont l'absence exclut la *misdelivery* de la
couverture P&I : sans registre, le transporteur ne peut pas établir à qui, quand et
comment il a remis les originaux.

Trois contraintes sont posées **en base**, pas seulement dans les formulaires :

- ``ck_bl_receipt_channel`` — le canal appartient à la liste fermée. Les trois
  canaux n'ont **pas la même valeur probante** (``download`` = accès,
  ``client_confirmed`` = déclaration du client, ``ops_confirmed`` = attestation de
  repli) et les confondre viderait le registre de son sens.
- ``ck_bl_receipt_confirmer_client_xor_staff`` — le confirmateur est **soit** le
  client, **soit** le staff, jamais les deux : une attestation du staff ne doit
  jamais pouvoir être présentée comme une déclaration du client. Même principe que
  ``bl_validated_on_behalf_by_id``.
- ``ck_bl_receipt_ops_needs_means`` — un repli Opérations **sans moyen de remise**
  n'établit rien ; il est donc instockable.

Table **append-only** par conception : on n'écrase pas un événement de remise, on en
ajoute un. ``ON DELETE CASCADE`` sur le lot, car un registre orphelin de son
connaissement n'a pas de sens.

Réversible : ``downgrade`` supprime la table (et donc ses contraintes/index).

Revision ID: 20260817_0116
Revises: 20260817_0115
Create Date: 2026-08-17 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260817_0116"
down_revision = "20260817_0115"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bl_delivery_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("packing_list_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("means", sa.String(60), nullable=True),
        sa.Column(
            "confirmed_by_client_id",
            sa.Integer(),
            sa.ForeignKey("client_accounts.id"),
            nullable=True,
        ),
        sa.Column(
            "confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("confirmed_by_name", sa.String(200), nullable=True),
        sa.Column("attachment_path", sa.String(300), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(60), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "channel IN ('download', 'client_confirmed', 'ops_confirmed')",
            name="ck_bl_receipt_channel",
        ),
        sa.CheckConstraint(
            "confirmed_by_client_id IS NULL OR confirmed_by_user_id IS NULL",
            name="ck_bl_receipt_confirmer_client_xor_staff",
        ),
        sa.CheckConstraint(
            "channel <> 'ops_confirmed' OR means IS NOT NULL",
            name="ck_bl_receipt_ops_needs_means",
        ),
    )
    op.create_index("ix_bl_delivery_receipts_batch_id", "bl_delivery_receipts", ["batch_id"])
    op.create_index("ix_bl_delivery_receipts_channel", "bl_delivery_receipts", ["channel"])


def downgrade():
    op.drop_index("ix_bl_delivery_receipts_channel", table_name="bl_delivery_receipts")
    op.drop_index("ix_bl_delivery_receipts_batch_id", table_name="bl_delivery_receipts")
    op.drop_table("bl_delivery_receipts")
