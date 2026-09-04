"""Fusion des têtes Alembic QHSE × commercial (no-op).

**Cinquième occurrence du même mode de panne**, et la troisième en deux jours.
``20260903_0141`` (réconciliation des ré-imports QHSE, PR #195) et
``20260904_0141`` (coût de revient et unité de vente des grilles, PR #196) sont
toutes deux chaînées sur ``20260903_0140``. Aucune des deux branches ne pouvait
voir l'autre : chacune avait une tête unique quand elle a été écrite, et la
sentinelle ``tests/regression/test_alembic_single_head.py`` est passée au vert
sur l'une comme sur l'autre. C'est leur **fusion** dans ``main`` qui a créé la
seconde tête, et ``alembic upgrade head`` a échoué au déploiement de
``473a36e`` : « Multiple head revisions are present for given argument 'head' ».

Les deux lots sont additifs et **disjoints** — tables et colonnes QHSE d'un
côté, ``rate_grid_lines.cost_rate`` / ``rate_unit`` et
``commercial_clients.pipedrive_synced_at`` de l'autre. Aucune opération de
schéma n'est donc nécessaire ici : cette révision ne fait que réunir les deux
chaînes.

**Fusion et non rechaînage**, pour la raison déjà posée en ``20260826_0119``,
``20260903_0139`` et ``20260903_0140`` : les deux révisions sont publiées sur
``main`` et ont pu être appliquées à une base de développement. Réécrire
l'ascendance de l'une ferait considérer l'autre chaîne comme appliquée sur une
telle base, dont les tables manqueraient **silencieusement**.

**Pourquoi la sentinelle ne pouvait rien y faire, et ce qui le pourrait.**

``tests/regression/test_alembic_single_head.py`` lit le graphe **de la branche
courante**. Sur ``feat/qhse-import-reconciliation`` comme sur
``claude/commercial-module-improvements-9u3ndb``, ce graphe n'a qu'une tête :
chacune ignore le fichier de l'autre. Aucun test exécuté depuis une seule
branche ne peut voir la collision — et un contrôle « deux révisions ne
partagent pas leur parent » n'ajouterait rien, puisqu'il ne fait que redire la
tête multiple une fois les deux fichiers réunis.

La CI *tourne* pourtant sur le commit de fusion (``on: pull_request`` : GitHub
teste ``refs/pull/N/merge``). Mais elle l'a fait pour la PR #196 **avant** que
la PR #195 ne soit fusionnée : son verdict portait sur un ``main`` qui ne
contenait pas encore ``20260903_0141``, et il n'a pas été redemandé ensuite.

Le remède structurel n'est donc pas un test de plus : c'est le réglage de
protection de branche **« Require branches to be up to date before merging »**
sur ``main``, qui force la CI à rejouer sur l'état réel d'après-fusion. C'est
une décision de gouvernance du dépôt, pas une ligne de code — elle est portée à
l'arbitrage de Julien.

Le doublon de numérotation (``0141`` porté par les deux) est le symptôme visible
du même angle mort : le préfixe est daté, donc deux branches écrites à un jour
d'écart tombent naturellement sur le même rang. Les deux fichiers sont publiés :
on ne les renomme pas.

Revision ID: 20260904_0142
Revises: 20260903_0141, 20260904_0141
Create Date: 2026-09-04 12:05:00.000000

"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260904_0142"
down_revision = ("20260903_0141", "20260904_0141")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Aucune opération : cette révision ne fait que réunir deux chaînes."""


def downgrade() -> None:
    """Aucune opération : la fusion se défait en revenant sur chaque chaîne."""
