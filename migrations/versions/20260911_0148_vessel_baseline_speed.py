"""Vitesse d'essai par navire — base de décarbonation « sans voiles ».

Ajoute ``vessels.baseline_speed_kn`` et ``vessels.baseline_speed_source``, puis
sème les trois navires en service à partir des **fichiers EEDI visés par Bureau
Veritas** (méthodologie de performance environnementale v3.0 §11.2, A9).

🔴 **Pourquoi une vitesse PAR NAVIRE.** ANEMOS et ARTEMIS sont sisterships sur
tout ce que les fichiers enregistrent — 429 kW par moteur, 212 g/kWh, Lpp
64,725 m, bau 12,6 m, tirant d'eau 4,9 m, déplacement 2 619 t. Mais leurs
courbes vitesse/puissance diffèrent **systématiquement** d'environ 12 % :
ARTEMIS demande 698 kW là où ANEMOS en demande 796 à 11 nœuds. Chaque navire
ajuste sa propre courbe avec un R² de 0,999 et 0,995 ; une courbe commune tombe
à 0,902, avec un décalage régulier. Prendre la vitesse d'ANEMOS pour toute la
flotte flattait ARTEMIS de **2,6 points** de taux de décarbonation.

Valeurs semées, et leur provenance :

- **ANEMOS** (IMO 9982938) — 11,36 kn, point **mesuré** à 100 % MCR, REF-07
  § 3.1.3.3 (doc. C413-1000-16 rev B, PIRIOU, visé BV le 23/07/2024) ;
- **ARTEMIS** (IMO 9983798) — 12,07 kn, sa propre courbe (R² 0,995) résolue à
  858 kW, REF-08 § 3.1.3.3 (doc. C432-1000-16 rev B, visé BV le 22/07/2024) ;
- **ATLANTIS** (IMO 1094917) — 11,86 kn, **courbe de classe** ajustée sur les
  six points des deux navires documentés (``P = 5,151·V^2,068``), faute de
  fichier propre. C'est le paramètre le plus pertinent pour un membre non mesuré
  de la série : prendre la courbe d'un sistership préjugerait auquel des deux il
  ressemble, alors qu'ils diffèrent de 12 %. **À remplacer dès réception de son
  fichier** — faiblesse W3 de l'audit, ouverte pour ce seul navire.

ATLAS n'est **pas** semé : il n'est pas en service.

Rattachement par ``imo_number`` et non par ``code`` : l'IMO est l'identifiant
officiel, celui que porte la fiche THETIS-MRV d'où proviennent les
caractéristiques de classe. Les valeurs sont **en dur** — une migration est un
instantané, jamais un appel au code vivant.

Idempotente : ne sème que les lignes dont la vitesse est encore ``NULL``, donc
n'écrase jamais une valeur corrigée à la main.

⚠️ **Le seed peut ne rien toucher, et il le fera en silence.** Le rattachement
se fait par ``imo_number``, colonne ``String(20)`` **libre et nullable** :
rien dans le dépôt ne crée les navires en production, donc le format qui y est
réellement stocké n'est pas vérifiable d'ici. Si la production écrivait
``IMO 9982938``, ou si ATLANTIS n'est pas encore créé, l'``UPDATE`` ne
toucherait rien et la migration réussirait quand même — le taux de
décarbonation retomberait alors sur une absence motivée sans qu'on sache
pourquoi.

**Contrôle post-migration obligatoire** : ``SELECT code, imo_number,
baseline_speed_kn FROM vessels`` — les trois navires en service doivent
porter une vitesse.

Revision ID: 20260911_0148
Revises: 20260911_0147
"""

from decimal import Decimal

from alembic import op
import sqlalchemy as sa

revision = "20260911_0148"
down_revision = "20260911_0147"
branch_labels = None
depends_on = None

# 🔴 La vitesse est un ``Decimal``, jamais une chaîne — et son paramètre porte
# son type explicitement (``sa.Numeric(6, 3)``).
#
# C'est le défaut qui a mis ce déploiement à terre le 17/09/2026. Un
# ``bindparams(speed="11.360")`` laisse SQLAlchemy déduire le type du type
# Python de la valeur : ``str`` ⇒ ``String``. asyncpg rend alors le paramètre
# ``$1::VARCHAR``, et PostgreSQL refuse d'affecter un ``character varying`` à
# une colonne ``numeric`` (aucune conversion implicite en contexte
# d'affectation) : ``DatatypeMismatchError``.
#
# ⚠️ **L'échec est à la préparation de la requête, pas à son exécution** : il
# ne dépend d'aucune donnée et se produit même sur une table ``vessels``
# vide. Il est donc certain sur toute base PostgreSQL — et invisible partout
# où le moteur est typé dynamiquement.
_BASELINE_SEED: tuple[tuple[str, Decimal, str], ...] = (
    (
        "9982938",
        Decimal("11.360"),
        "REF-07 §3.1.3.3 — C413-1000-16 rev B, vise BV 23/07/2024 (mesure)",
    ),
    (
        "9983798",
        Decimal("12.070"),
        "REF-08 §3.1.3.3 — C432-1000-16 rev B, vise BV 22/07/2024 (courbe R2 0,995)",
    ),
    (
        "1094917",
        Decimal("11.860"),
        "Courbe de classe P=5,151V^2,068 sur REF-07+REF-08 — fichier propre attendu",
    ),
)


def upgrade() -> None:
    op.add_column("vessels", sa.Column("baseline_speed_kn", sa.Numeric(6, 3), nullable=True))
    op.add_column("vessels", sa.Column("baseline_speed_source", sa.String(120), nullable=True))

    for imo, speed, source in _BASELINE_SEED:
        op.execute(
            sa.text(
                "UPDATE vessels SET baseline_speed_kn = :speed, baseline_speed_source = :source "
                "WHERE imo_number = :imo AND baseline_speed_kn IS NULL"
            ).bindparams(
                # Type explicite : il décide du cast rendu au pilote. Le
                # déduire de la valeur Python suffit ici (un ``Decimal`` donne
                # déjà ``NUMERIC``), mais le dire supprime la dépendance au
                # type de la constante — c'est elle qui a cédé.
                sa.bindparam("speed", speed, type_=sa.Numeric(6, 3)),
                sa.bindparam("source", source, type_=sa.String(120)),
                sa.bindparam("imo", imo, type_=sa.String(20)),
            )
        )


def downgrade() -> None:
    """🔴 Détruit toute vitesse corrigée à la main, y compris celle qu'on attend.

    L'``upgrade`` annonce qu'ATLANTIS **doit** être corrigé dès réception de
    son fichier EEDI (faiblesse W3). Ce ``downgrade`` supprime les colonnes :
    cette correction, et toute autre, sont perdues sans trace.

    À relever avant de le jouer : ``SELECT code, imo_number, baseline_speed_kn,
    baseline_speed_source FROM vessels``.
    """
    op.drop_column("vessels", "baseline_speed_source")
    op.drop_column("vessels", "baseline_speed_kn")
