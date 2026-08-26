"""Estimation tarifaire — origine, rattachement client, fiches prospect.

Lot 6 de la refonte commerciale.

Le « devis » devient l'**estimation tarifaire**, et son parcours se scinde en
deux, délibérément asymétriques :

* **extranet** — un client authentifié estime en libre-service sur **ses**
  grilles actives ; le prix s'affiche immédiatement ;
* **public_request** — un visiteur sans compte dépose une demande depuis la
  vitrine. **Aucun prix ne lui est affiché** : la demande crée une fiche
  prospect, le commercial qualifie, ouvre un extranet, puis propose une offre.

Le formulaire public chiffrait jusqu'ici immédiatement — et, si le visiteur se
trouvait connecté, sur la grille négociée de son client. Publier un tarif à qui
n'a pas été identifié expose la politique tarifaire à n'importe qui.

Reprise des données : les estimations existantes reçoivent l'origine
``extranet`` **lorsqu'un compte client est rattaché**, ``public_request`` sinon —
c'est exactement la distinction que faisait déjà le parcours, on ne fait que la
nommer. ``commercial_client_id`` est rempli depuis le compte plateforme quand le
lien existe ; il reste vide sinon, plutôt que d'être deviné.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260826_0124"
down_revision = "20260826_0123"
branch_labels = None
depends_on = None


def upgrade():
    # ── Estimations ──────────────────────────────────────────────────────
    with op.batch_alter_table("quotes") as batch:
        batch.add_column(
            sa.Column(
                "origin", sa.String(length=20), nullable=False, server_default="extranet"
            )
        )
        batch.add_column(sa.Column("commercial_client_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("converted_offer_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_quotes_commercial_client",
            "commercial_clients",
            ["commercial_client_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_quotes_converted_offer",
            "rate_offers",
            ["converted_offer_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_quotes_origin", "quotes", ["origin"])
    op.create_index("ix_quotes_commercial_client_id", "quotes", ["commercial_client_id"])

    # ── Fiches prospect ──────────────────────────────────────────────────
    with op.batch_alter_table("commercial_clients") as batch:
        batch.add_column(
            sa.Column("is_prospect", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("prospect_source", sa.String(length=40), nullable=True))
    op.create_index("ix_commercial_clients_is_prospect", "commercial_clients", ["is_prospect"])

    conn = op.get_bind()
    # Origine : un compte client rattaché ⇒ estimation extranet ; sinon demande publique.
    conn.execute(
        sa.text(
            "UPDATE quotes SET origin = 'public_request' WHERE client_account_id IS NULL"
        )
    )
    # Rattachement commercial repris du compte plateforme, quand il existe.
    conn.execute(
        sa.text(
            """
            UPDATE quotes
               SET commercial_client_id = ca.commercial_client_id
              FROM client_accounts ca
             WHERE ca.id = quotes.client_account_id
               AND ca.commercial_client_id IS NOT NULL
            """
        )
    )


def downgrade():
    op.drop_index("ix_commercial_clients_is_prospect", table_name="commercial_clients")
    with op.batch_alter_table("commercial_clients") as batch:
        batch.drop_column("prospect_source")
        batch.drop_column("is_prospect")

    op.drop_index("ix_quotes_commercial_client_id", table_name="quotes")
    op.drop_index("ix_quotes_origin", table_name="quotes")
    with op.batch_alter_table("quotes") as batch:
        batch.drop_constraint("fk_quotes_converted_offer", type_="foreignkey")
        batch.drop_constraint("fk_quotes_commercial_client", type_="foreignkey")
        batch.drop_column("converted_offer_id")
        batch.drop_column("commercial_client_id")
        batch.drop_column("origin")
