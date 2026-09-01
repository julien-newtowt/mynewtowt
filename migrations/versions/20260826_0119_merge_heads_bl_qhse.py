"""Fusion des têtes Alembic BL × QHSE (no-op).

Le lot « workflow Bill of Lading » (#158, ``20260814_0114`` →
``20260817_0118``) et le lot « QHSE fondations Phase 0 » (#160,
``20260722_0106``) ont tous deux été chaînés sur ``20260807_0113`` : ce sont
des **frères, pas une file**. ``main`` a absorbé le premier sans rien dire ;
le second y est arrivé avec un parent qui n'était plus la tête et a donc
**recréé deux têtes Alembic** — ``alembic upgrade head`` refuse alors de
choisir (« Multiple head revisions are present »), échec constaté au
déploiement de 96a5c70 le 26/08/2026 (rollback automatique sur snapshot).
Même panne qu'au 07/08/2026, cf. ``20260807_0113``.

Cette révision ne porte **aucune** opération de schéma : elle réunit les deux
chaînes pour rendre l'historique linéaire à nouveau. Les deux lots sont
additifs et **disjoints** (tables ``qhse_*``/``deficiency_codes`` d'un côté,
états et révisions de BL sur ``packing_list_batches``/``bl_*`` de l'autre) :
aucun ordre d'application n'est requis entre eux.

Pourquoi une fusion et non un rechaînage de QHSE sur ``20260817_0118`` : les
deux révisions sont désormais **publiées sur `main`**. Réécrire l'ascendance
de ``20260722_0106`` ferait considérer les migrations BL comme déjà
appliquées sur toute base qui porte déjà QHSE (poste de dev, staging) — les
tables BL y manqueraient **silencieusement**. La fusion, elle, est correcte
quel que soit l'état de la base.

Revision ID: 20260826_0119
Revises: 20260817_0118, 20260722_0106
Create Date: 2026-08-26 00:00:00.000000

"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260826_0119"
down_revision = ("20260817_0118", "20260722_0106")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
