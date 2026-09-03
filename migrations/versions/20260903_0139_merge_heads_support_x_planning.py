"""Fusion des têtes Alembic Assistance × planification (no-op).

Le module « Assistance » (#173, ``20260821_0119``) et la reprise d'historique
TOWT / séquence de planification (#176 → #181, ``20260901_0137`` puis
``20260902_0138``) ont tous deux été chaînés sur ``20260901_0136`` : ce sont
des **frères, pas une file**. ``main`` a absorbé la seconde chaîne d'abord ;
``0119`` y est arrivée avec un parent qui n'était plus la tête et a donc
**recréé deux têtes** — ``alembic upgrade head`` refuse alors de choisir
(« Multiple head revisions are present »), échec constaté au déploiement de
``1d480c6`` le 03/09/2026 à 09:28 UTC (restauration automatique du snapshot,
image non permutée, base saine). Troisième occurrence de cette panne, après
celles du 07/08 (``20260807_0113``) et du 26/08 (``20260826_0119``).

Cette révision ne porte **aucune** opération de schéma : elle réunit les deux
chaînes pour rendre l'historique linéaire à nouveau. Les deux lots sont
additifs et **disjoints** — tables ``support_tickets`` /
``support_ticket_comments`` / ``support_ticket_attachments`` d'un côté,
colonnes ``legs.voyage_completed_at`` et ``legs.origin`` (+ index et trigger
d'immuabilité des archives TOWT) de l'autre. Aucun ordre d'application n'est
requis entre eux.

**Pourquoi une fusion et non un rechaînage de ``0119`` sur ``20260902_0138``.**
Les deux révisions sont désormais **publiées sur `main`**. Réécrire
l'ascendance de ``0119`` ferait considérer ``0137`` et ``0138`` comme déjà
appliquées sur toute base qui porte déjà ``0119`` — et il en existe
probablement : sur la branche ``feature/support-ticketing``, ``0119`` était
tête unique, donc un ``alembic upgrade head`` y réussissait. Les colonnes de
planification y manqueraient **silencieusement**. La fusion, elle, est
correcte quel que soit l'état de la base : une base à ``0138`` applique
``0119`` puis ce point de fusion, une base à ``0119`` applique ``0137``,
``0138`` puis ce point de fusion.

⚠️ Conséquence d'exploitation d'un point de fusion, déjà documentée au runbook
§6.3 : ``alembic downgrade -1`` échoue ici en « Ambiguous walk » — le pas
relatif ne sait pas laquelle des deux branches remonter. Il faut nommer la
cible (``alembic downgrade 20260902_0138``, ou ``20260821_0119``).

Revision ID: 20260903_0139
Revises: 20260821_0119, 20260902_0138
Create Date: 2026-09-03 09:32:33.131531

"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260903_0139"
down_revision = ("20260821_0119", "20260902_0138")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Aucune opération : cette révision ne fait que réunir deux chaînes."""


def downgrade() -> None:
    """Aucune opération : défaire la fusion se fait en nommant la branche cible."""
