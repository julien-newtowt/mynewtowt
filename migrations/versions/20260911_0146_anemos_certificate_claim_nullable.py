"""Certificat Anemos : la comparaison conventionnelle devient facultative.

🔴 **Motif : risque d'allégation environnementale.**

``co2_conventional_kg`` et ``co2_avoided_kg`` étaient ``NOT NULL`` : un
certificat ne pouvait pas exister sans porter une comparaison à un
**porte-conteneurs conventionnel à 13,7 gCO₂/t·km**.

Or cette base de comparaison a été **écartée** par la *Environmental
Performance Methodology v3.0* (§11.1, décision du 08/09/2026) : la relation
entre la taille d'un navire et son facteur d'émission n'étant pas linéaire, le
segment retenu détermine le résultat sur une plage de 87 % à 96 %. La
méthodologie ajoute « ne pas réintroduire cette base sans une nouvelle décision
explicite ». La directive (UE) 2024/825, en application pleine au 27/09/2026,
exige qu'une allégation environnementale soit substantiable.

Décision du 11/09/2026 : **le certificat ne porte plus cette comparaison**. Il
conserve ce qui est **mesuré** — tonnage, distance, CO₂ réellement émis — et
abandonne ce qui relevait d'un **contrefactuel** sur une base retirée.

Cette migration ne fait que **permettre** l'absence : elle ne touche à aucune
valeur existante. Les certificats déjà émis gardent les leurs en base (trace de
ce qui a été calculé), mais l'application cesse de les **publier** — une colonne
n'est pas une allégation, un PDF remis à un client en est une.

La réversibilité est assumée et documentée dans ``downgrade``.

Revision ID: 20260911_0146
Revises: 20260907_0145
"""

from alembic import op
import sqlalchemy as sa

revision = "20260911_0146"
down_revision = "20260907_0145"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "anemos_certificates",
        "co2_conventional_kg",
        existing_type=sa.Numeric(10, 3),
        nullable=True,
    )
    op.alter_column(
        "anemos_certificates",
        "co2_avoided_kg",
        existing_type=sa.Numeric(10, 3),
        nullable=True,
    )


def downgrade() -> None:
    """🔴 Le retour arrière n'est PAS sûr, et c'est délibérément dit ici.

    Les certificats émis après cette migration portent ``NULL`` sur ces deux
    colonnes. Reposer la contrainte ``NOT NULL`` échouerait sur eux — et la
    seule façon de la faire passer serait d'inventer une valeur, c'est-à-dire
    de **fabriquer l'allégation** que cette migration retire.

    On rétablit donc la contrainte en remplissant les lignes concernées par
    ``0``, valeur qui se lit sans ambiguïté comme « aucune comparaison » et non
    comme « comparaison à zéro ». Si ce downgrade est joué, il faut savoir que
    ces zéros ne sont pas des mesures.
    """
    op.execute(
        "UPDATE anemos_certificates "
        "SET co2_conventional_kg = 0 WHERE co2_conventional_kg IS NULL"
    )
    op.execute("UPDATE anemos_certificates SET co2_avoided_kg = 0 WHERE co2_avoided_kg IS NULL")
    op.alter_column(
        "anemos_certificates",
        "co2_avoided_kg",
        existing_type=sa.Numeric(10, 3),
        nullable=False,
    )
    op.alter_column(
        "anemos_certificates",
        "co2_conventional_kg",
        existing_type=sa.Numeric(10, 3),
        nullable=False,
    )
