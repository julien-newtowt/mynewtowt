"""Vente à bord — remise, ligne hors catalogue, seuil d'alerte, rendu de monnaie.

Quatre manques P1 de l'audit du 2026-08-27, réunis parce qu'ils touchent les
mêmes tables :

* ``onboard_sale_lines.discount_pct`` — aucune remise ni gratuité n'était
  possible. Un geste commercial ou un article offert à l'équipage imposait de
  créer un faux produit. Le total de ligne reste **dérivé** de
  (prix × quantité × remise) : rien n'est saisi librement, et 100 % vaut
  gratuité.
* La **ligne hors catalogue** n'a pas besoin de colonne : ``product_id`` est
  nullable depuis l'origine. C'est la route qui manquait, pas le schéma.
* ``onboard_products.min_stock_alert`` — une rupture ne se découvrait qu'au
  moment de vendre. ``NULL`` = pas de suivi d'alerte pour cet article.
* ``onboard_sales.cash_received`` — les espèces remises par l'acheteur, pour
  calculer le rendu. Purement informatif : la caisse est créditée du **total de
  la vente**, jamais de ce montant. Sans cette trace, un écart de rendu de
  monnaie restait inexplicable au comptage.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0134"
down_revision = "20260827_0133"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "onboard_sale_lines",
        sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_onboard_sale_line_discount_range",
        "onboard_sale_lines",
        "discount_pct >= 0 AND discount_pct <= 100",
    )
    op.add_column(
        "onboard_products", sa.Column("min_stock_alert", sa.Numeric(12, 3), nullable=True)
    )
    op.add_column("onboard_sales", sa.Column("cash_received", sa.Numeric(12, 2), nullable=True))


def downgrade():
    op.drop_column("onboard_sales", "cash_received")
    op.drop_column("onboard_products", "min_stock_alert")
    op.drop_constraint("ck_onboard_sale_line_discount_range", "onboard_sale_lines", type_="check")
    op.drop_column("onboard_sale_lines", "discount_pct")
