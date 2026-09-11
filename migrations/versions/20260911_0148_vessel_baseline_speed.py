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

Revision ID: 20260911_0148
Revises: 20260911_0147
"""

from alembic import op
import sqlalchemy as sa

revision = "20260911_0148"
down_revision = "20260911_0147"
branch_labels = None
depends_on = None

# (imo_number, vitesse d'essai en nœuds, source documentaire)
_BASELINE_SEED: tuple[tuple[str, str, str], ...] = (
    ("9982938", "11.360", "REF-07 §3.1.3.3 — C413-1000-16 rev B, vise BV 23/07/2024 (mesure)"),
    ("9983798", "12.070", "REF-08 §3.1.3.3 — C432-1000-16 rev B, vise BV 22/07/2024 (courbe R2 0,995)"),
    ("1094917", "11.860", "Courbe de classe P=5,151V^2,068 sur REF-07+REF-08 — fichier propre attendu"),
)


def upgrade() -> None:
    op.add_column("vessels", sa.Column("baseline_speed_kn", sa.Numeric(6, 3), nullable=True))
    op.add_column("vessels", sa.Column("baseline_speed_source", sa.String(120), nullable=True))

    for imo, speed, source in _BASELINE_SEED:
        op.execute(
            sa.text(
                "UPDATE vessels SET baseline_speed_kn = :speed, baseline_speed_source = :source "
                "WHERE imo_number = :imo AND baseline_speed_kn IS NULL"
            ).bindparams(speed=speed, source=source, imo=imo)
        )


def downgrade() -> None:
    op.drop_column("vessels", "baseline_speed_source")
    op.drop_column("vessels", "baseline_speed_kn")
