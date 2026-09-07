"""Grilles tarifaires : coût de revient et unité de vente par route (COM-12).

Inversion de la logique de tarification. Jusqu'ici ``rate_grid_lines.base_rate``
portait **deux sens à la fois** : le coût calculé (OPEX × jours de mer /
capacité) quand ``is_manual`` était faux, le prix décidé par le commercial
quand il était vrai. La marge n'était donc jamais lisible : dans le premier cas
elle valait zéro par construction, dans le second le coût avait disparu.

Deux colonnes séparent les deux notions :

- ``cost_rate`` — coût de revient calculé, **nullable à dessein**. ``NULL``
  signifie « capacité de référence inconnue » (port en lourd absent du
  référentiel flotte pour une route au poids) : l'écran affiche « — » plutôt
  qu'un coût inventé, et la marge n'est pas calculée.
- ``rate_unit`` — unité de vente de la route : ``palette`` (défaut historique,
  et seule valeur possible avant cette révision) ou ``tonne``.

Reprise des données : pour les routes **non manuelles**, ``base_rate`` *était*
le coût OPEX — il est recopié dans ``cost_rate``, ce qui rend la marge
immédiatement lisible (0 %, ce qui est la vérité : ces routes se vendaient à
prix coûtant). Pour les routes **manuelles**, le coût n'a jamais été conservé :
``cost_rate`` reste ``NULL`` jusqu'au prochain recalcul depuis l'écran, qui le
posera sans toucher au prix.

Ajoute aussi ``commercial_clients.pipedrive_synced_at`` : la fiche client est
désormais alimentée par Pipedrive (contact, téléphone, pays), et l'écran doit
pouvoir dire de quand date ce qu'il affiche.

Revision ID: 20260904_0141
Revises: 20260903_0140
Create Date: 2026-09-04 08:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260904_0141"
down_revision = "20260903_0140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rate_grid_lines", sa.Column("cost_rate", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "rate_grid_lines",
        sa.Column(
            "rate_unit",
            sa.String(length=10),
            nullable=False,
            server_default="palette",
        ),
    )
    # Reprise : le base_rate d'une route non manuelle EST le coût OPEX.
    op.execute(
        "UPDATE rate_grid_lines SET cost_rate = base_rate WHERE is_manual = false"
    )
    op.add_column(
        "commercial_clients",
        sa.Column("pipedrive_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("commercial_clients", "pipedrive_synced_at")
    op.drop_column("rate_grid_lines", "rate_unit")
    op.drop_column("rate_grid_lines", "cost_rate")
