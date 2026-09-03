"""Fusion des têtes Alembic legacy MRV × Assistance/planification (no-op).

Quatrième occurrence du même mode de panne, et la seconde de la journée.
``20260713_0106`` (retrait du legacy MRV, PR #174) était le **troisième** enfant
de ``20260901_0136``, aux côtés de ``20260821_0119`` (Assistance) et de
``20260901_0137`` (planification). La révision de fusion ``20260903_0139`` n'a
réuni que les deux premières — la PR #174 n'était pas encore fusionnée quand
elle a été écrite. ``main`` s'est donc retrouvé avec deux têtes de nouveau, et
``alembic upgrade head`` a échoué au déploiement de ``1f082f9`` le 03/09/2026 à
10:41 UTC (restauration automatique du snapshot, image non permutée, base
saine).

Cette révision ne porte **aucune** opération de schéma : elle réunit les deux
chaînes restantes. Les lots sont additifs et **disjoints** — renommage des
tables ``mrv_events``/``mrv_parameters`` d'un côté, tables ``support_*`` et
colonnes ``legs.voyage_completed_at``/``legs.origin`` de l'autre.

Fusion et non rechaînage, pour la raison déjà posée dans ``20260826_0119`` et
``20260903_0139`` : ``20260713_0106`` est désormais publiée sur ``main``, et
elle a pu être appliquée à une base de développement depuis
``feature/dashboard-env-integration``, où elle était tête unique avant que la
branche n'intègre ``main``. Réécrire son ascendance ferait considérer les
autres chaînes comme appliquées sur une telle base, dont les tables
manqueraient **silencieusement**.

**Leçon de méthode.** Une révision de fusion ne vaut que pour les têtes
existantes **au moment où elle est écrite**. Tant que d'autres branches portent
une migration chaînée sur le même parent, chaque fusion en recrée une. Le seul
remède structurel est de rendre le verdict de la CI opposable : la sentinelle
``tests/regression/test_alembic_single_head.py`` a signalé les quatre
occurrences, sans jamais pouvoir bloquer une fusion.

Revision ID: 20260903_0140
Revises: 20260713_0106, 20260903_0139
Create Date: 2026-09-03 10:45:00.000000

"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260903_0140"
down_revision = ("20260713_0106", "20260903_0139")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Aucune opération : cette révision ne fait que réunir deux chaînes."""


def downgrade() -> None:
    """Aucune opération : défaire la fusion se fait en nommant la branche cible."""
