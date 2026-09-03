"""Mise à l'écart du legacy MRV — mrv_events / mrv_parameters renommées.

Le décommissionnement du lot 14 (migration ``20260709_0105``) avait
volontairement CONSERVÉ ``mrv_events``/``mrv_parameters`` en archive lecture
seule, faute de preuve qu'elles pouvaient être supprimées sans risque.

Vérification approfondie complémentaire (2026-07-13) : aucune preuve que
``MRVEvent`` a jamais servi à une déclaration réglementaire réellement soumise
aux autorités — la déclaration EU MRV 2025 a été produite par un outil externe
(« OVDAdmin »), pas par mynewtowt. Le CRUD manuel de ``MRVEvent`` n'a existé
que ~2,5 semaines, et aucune migration ne contient de données réelles insérées
(DDL uniquement). Le code applicatif associé — modèle, services
``mrv_compute``/``mrv_sync``, écran d'archive — est supprimé par la PR #174.

``decimal_to_dms`` (seule fonction encore active, exports OVDLA/OVDBR) a été
déplacée vers ``app.utils.geo`` avant cette migration.

**Pourquoi un renommage et non un DROP (arbitrage du 2026-09-03).**

Cette migration portait initialement ``op.drop_table()`` sur les deux tables.
Elle assortissait ce geste d'un « GATE HUMAIN » — vérifier ``COUNT(*)`` en
production avant d'appliquer — qui n'était **qu'une docstring** : ni le code,
ni la CI, ni ``scripts/deploy.sh`` ne l'appliquaient. Concrètement, le premier
``alembic upgrade head`` réussi après la fusion de la PR #174 aurait supprimé
les deux tables sans que personne ait produit ce comptage.

Or la justification du DROP établit une **absence de preuve d'usage**, pas une
preuve d'absence d'usage. Et l'arbitrage Q1 (« MRV v2 — démarrage à vide en
production »), invoqué à son appui, ne le couvre pas : Q1 porte sur la capture
v2 (``nav_events``, ``bunker_operations``), pas sur ces tables **legacy V1**,
antérieures à la refonte — que ``20260709_0105`` avait justement choisi de
conserver.

Le renommage atteint **100 % de l'objectif technique** : plus aucun rail de
lecture, modèle et services retirés de l'application, schéma applicatif
nettoyé côté ORM. Il diffère seulement l'irréversible. Si l'analyse est juste,
le coût est nul — deux tables orphelines qu'aucun code ne référence ; si elle a
manqué quelque chose, elle sauve une déclaration réglementaire.

**Cette migration est donc réversible, données comprises** : ``downgrade()``
restitue les tables et leur contenu, ce qu'un ``create_table`` après DROP ne
pouvait pas faire.

**Suite à donner.** Une fois ``SELECT count(*) FROM mrv_events_deprecated_20260903;``
et son équivalent ``mrv_parameters_deprecated_20260903`` relevés en production
et consignés, la suppression sèche tient en une migration d'une ligne par
table. Tant que ce comptage n'est pas fait, ne pas la poser.

Revision ID: 20260713_0106
Revises: 20260901_0136
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260713_0106"
down_revision = "20260901_0136"
branch_labels = None
depends_on = None

# Suffixe daté du jour de l'arbitrage : il dit quand la table est sortie du
# périmètre applicatif, et évite toute collision avec un futur usage du nom.
_RENAMES: tuple[tuple[str, str], ...] = (
    ("mrv_events", "mrv_events_deprecated_20260903"),
    ("mrv_parameters", "mrv_parameters_deprecated_20260903"),
)


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # Garde de tolérance : une base de développement ayant appliqué la version
    # « DROP » de cette révision avant l'arbitrage n'a plus les tables. Le
    # renommage y serait une erreur, pas un incident — on passe.
    existing = _tables()
    for source, target in _RENAMES:
        if source not in existing:
            continue
        if target in existing:
            continue
        op.rename_table(source, target)


def downgrade() -> None:
    existing = _tables()
    for source, target in _RENAMES:
        if target in existing and source not in existing:
            op.rename_table(target, source)
