"""Fusion des deux têtes de migration divergentes (aucun DDL).

``alembic upgrade head`` échouait sur ``main`` avec « Multiple head revisions
are present for given argument 'head' » : deux chaînes de migration avaient
divergé sans jamais être rebasées l'une sur l'autre.

- ``20260716_0112`` — chaîne MRV (dernier maillon : ``nav_event_noon.rob_uree_t``
  / ``rob_eau_douce_t``, ROB annexes G5).
- ``20260720_0107`` — chaîne rapports générés (dernier maillon : table
  ``generated_reports``, archivage des PDF serveur / trombinoscope).

Conséquence avant ce correctif : la production utilisant Alembic exclusivement
(cf. ``CLAUDE.md`` §Patterns critiques), **tout déploiement par
``alembic upgrade head`` était bloqué**, et toute nouvelle migration exigeait de
préciser explicitement sa tête cible.

Cette révision est une **migration de fusion pure** : elle ne porte aucun DDL,
son seul rôle est de raccorder les deux chaînes en une tête unique. Les deux
chaînes touchent des tables disjointes (``nav_event_noon`` d'un côté,
``generated_reports`` de l'autre) — leur ordre d'application relatif est donc
indifférent, ce qui rend la fusion sûre.

``upgrade``/``downgrade`` sont volontairement vides : il n'y a rien à appliquer
ni à annuler. ``downgrade`` ramène simplement l'historique aux deux têtes.

Revision ID: 20260730_0113
Revises: ('20260716_0112', '20260720_0107')
Create Date: 2026-07-30 00:00:00.000000

"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260730_0113"
down_revision = ("20260716_0112", "20260720_0107")
branch_labels = None
depends_on = None


def upgrade():
    """Aucun DDL — fusion d'historique uniquement."""


def downgrade():
    """Aucun DDL — la rétrogradation rétablit les deux têtes divergentes."""
