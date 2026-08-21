"""Support applicatif (« Assistance ») — demandes, échanges, pièces jointes.

Crée les trois tables du module ``support`` :

- ``support_tickets`` — la demande, sa machine à états, et son **contexte
  technique** (écran, navigateur, version) sans lequel un signalement est
  inexploitable : rien ne capte les erreurs applicatives aujourd'hui.
- ``support_ticket_comments`` — fil d'échanges, avec ``is_internal`` pour les
  notes réservées à l'administrateur.
- ``support_ticket_attachments`` — captures d'écran et fichiers, stockés par
  ``services.safe_files`` (nom aléatoire sur disque, chemin relatif ici).

⚠️ Aucun rapport avec les tables ``tickets`` / ``ticket_comments`` du module
d'incidents d'escale. Cf. ``docs/strategy/SPEC_SUPPORT_TICKETING.md`` §1.

La séquence de référence est portée par ``(seq_year, seq_number)`` avec une
contrainte d'unicité : ``MAX(seq_number) + 1`` ne recycle pas les numéros, et
une collision concurrente échoue bruyamment au lieu de produire un doublon.

**Archivage** : aucune colonne. C'est un état dérivé (état terminal + 90 jours),
donc rien à migrer — cf. ``services.support.is_archived``.

Revision ID: 20260821_0119
Revises: 20260807_0113
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260821_0119"
down_revision = "20260807_0113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference", sa.String(length=20), nullable=False, unique=True),
        sa.Column("seq_year", sa.Integer(), nullable=False),
        sa.Column("seq_number", sa.Integer(), nullable=False),
        sa.Column(
            "reporter_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_support_reporter"),
            nullable=False,
        ),
        sa.Column("reporter_role", sa.String(length=30), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="nouveau"),
        sa.Column(
            "assigned_to_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_support_assignee"),
            nullable=True,
        ),
        sa.Column("resolution", sa.Text(), nullable=True),
        # Réservé à l'ouverture aux clients (hors v1) — jamais renseigné pour l'instant.
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("client_accounts.id", name="fk_support_client"),
            nullable=True,
        ),
        # Contexte technique, renseigné côté serveur.
        sa.Column("page_url", sa.String(length=500), nullable=True),
        sa.Column("http_referer", sa.String(length=500), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("app_version", sa.String(length=20), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("seq_year", "seq_number", name="uq_support_tickets_seq"),
    )
    op.create_index("ix_support_tickets_status", "support_tickets", ["status"])
    op.create_index(
        "ix_support_tickets_status_severity", "support_tickets", ["status", "severity"]
    )
    op.create_index("ix_support_tickets_reporter_id", "support_tickets", ["reporter_id"])
    op.create_index("ix_support_tickets_assigned_to_id", "support_tickets", ["assigned_to_id"])

    op.create_table(
        "support_ticket_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "support_ticket_id",
            sa.Integer(),
            sa.ForeignKey(
                "support_tickets.id", ondelete="CASCADE", name="fk_support_comment_ticket"
            ),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_support_comment_author"),
            nullable=True,
        ),
        sa.Column("author_name", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_support_ticket_comments_support_ticket_id",
        "support_ticket_comments",
        ["support_ticket_id"],
    )

    op.create_table(
        "support_ticket_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "support_ticket_id",
            sa.Integer(),
            sa.ForeignKey(
                "support_tickets.id", ondelete="CASCADE", name="fk_support_att_ticket"
            ),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(length=300), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=True),
        sa.Column("file_mime", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "uploaded_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_support_att_uploader"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_support_ticket_attachments_support_ticket_id",
        "support_ticket_attachments",
        ["support_ticket_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_support_ticket_attachments_support_ticket_id",
        table_name="support_ticket_attachments",
    )
    op.drop_table("support_ticket_attachments")
    op.drop_index(
        "ix_support_ticket_comments_support_ticket_id", table_name="support_ticket_comments"
    )
    op.drop_table("support_ticket_comments")
    op.drop_index("ix_support_tickets_assigned_to_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_reporter_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_status_severity", table_name="support_tickets")
    op.drop_index("ix_support_tickets_status", table_name="support_tickets")
    op.drop_table("support_tickets")
