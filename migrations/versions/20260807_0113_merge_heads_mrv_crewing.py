"""Fusion des têtes Alembic MRV × crewing (no-op).

Les lots « MRV gaps remediation » (…0111 → 20260716_0112) et « crewing
monthly yearbook » (…0106 → 20260720_0107) ont été développés en parallèle
et mergés séparément sur main : chacun a laissé une tête de migration, et
``alembic upgrade head`` refuse de choisir (« Multiple head revisions ») —
échec constaté au déploiement de bacb2f9 le 07/08/2026 (rollback snapshot
automatique). Cette révision de fusion ne porte AUCUNE opération de schéma :
elle réunit simplement les deux chaînes pour rendre l'historique linéaire à
nouveau. Les deux lots étant additifs et disjoints (colonnes nav_event_noon
d'un côté, table generated_reports de l'autre), aucun ordre d'application
n'est requis entre eux.

Revision ID: 20260807_0113
Revises: 20260716_0112, 20260720_0107
Create Date: 2026-08-07 00:00:00.000000

"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260807_0113"
down_revision = ("20260716_0112", "20260720_0107")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
