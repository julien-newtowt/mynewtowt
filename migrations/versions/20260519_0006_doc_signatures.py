"""Captain documents — signature & lock (SOF, noon report, watch log).

Ajoute aux 3 tables onboard :
  - signed_at, signed_by_id, signed_by_name
  - signature_hash (SHA-256 du contenu au moment de la signature)
  - is_locked (interdit toute modification / suppression post-signature)

Note : ``watch_logs.signed_at`` existait déjà comme timestamp serveur
(NOT NULL default now). On le garde tel quel (= horodatage création).
On ajoute juste les colonnes signed_by_*, signature_hash, is_locked.

Revision ID: 20260519_0006
Revises: 20260519_0005
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0006"
down_revision = "20260519_0005"
branch_labels = None
depends_on = None


def _add_sig_block(table: str, *, include_signed_at: bool) -> None:
    """Ajoute les colonnes de signature à une table."""
    cols = []
    if include_signed_at:
        cols.append(sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True))
    cols += [
        sa.Column(
            "signed_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("signed_by_name", sa.String(200), nullable=True),
        sa.Column("signature_hash", sa.String(64), nullable=True),
        sa.Column(
            "is_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    ]
    with op.batch_alter_table(table) as batch:
        for c in cols:
            batch.add_column(c)


def _drop_sig_block(table: str, *, include_signed_at: bool) -> None:
    cols = ["is_locked", "signature_hash", "signed_by_name", "signed_by_id"]
    if include_signed_at:
        cols.append("signed_at")
    with op.batch_alter_table(table) as batch:
        for c in cols:
            batch.drop_column(c)


def upgrade() -> None:
    # sof_events : pas de signed_at préexistant
    _add_sig_block("sof_events", include_signed_at=True)
    # noon_reports : pas de signed_at préexistant
    _add_sig_block("noon_reports", include_signed_at=True)
    # watch_logs : signed_at existe déjà (timestamp création) — ne pas le re-créer
    _add_sig_block("watch_logs", include_signed_at=False)


def downgrade() -> None:
    _drop_sig_block("watch_logs", include_signed_at=False)
    _drop_sig_block("noon_reports", include_signed_at=True)
    _drop_sig_block("sof_events", include_signed_at=True)
