"""MRV lot 14 — bascule capture événementielle & décommissionnement (nettoyage).

Migration de nettoyage de la bascule. **AUCUN DROP de table historique** :
``noon_reports``, ``mrv_events`` et ``mrv_parameters`` sont CONSERVÉES en archive
lecture seule (audit, signatures, fallback ledger ``legacy_noon``). Le
décommissionnement du legacy est purement applicatif (routes/écrans/sync retirés,
gardes de bascule côté serveur) — le schéma historique reste intact.

Seul ajout : un index de performance sur ``quality_check_results(acknowledged_at)``
pour la file « anomalies non acquittées » (tour de contrôle qualité de la
bascule : dashboard qualité, digest, resets en attente — filtres
``acknowledged_at IS NULL``). Les index utiles de la capture (``nav_events.status``,
etc.) existent déjà (lots 3/8), d'où l'unique ajout ici.

Revision ID: 20260709_0105
Revises: 20260709_0104
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260709_0105"
down_revision = "20260709_0104"
branch_labels = None
depends_on = None

_INDEX = "ix_qcr_acknowledged_at"
_TABLE = "quality_check_results"


def _index_names() -> set[str]:
    return {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(_TABLE)}


def upgrade() -> None:
    # Idempotent : ne recrée pas l'index s'il existe déjà (bases dev initialisées
    # via ``Base.metadata.create_all``, qui porte déjà l'index du modèle).
    if _INDEX not in _index_names():
        op.create_index(_INDEX, _TABLE, ["acknowledged_at"])


def downgrade() -> None:
    if _INDEX in _index_names():
        op.drop_index(_INDEX, table_name=_TABLE)
