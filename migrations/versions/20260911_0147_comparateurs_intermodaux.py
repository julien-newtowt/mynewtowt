"""Comparateurs intermodaux : porte-conteneurs retiré, aérien ramené à 630.

🔴 **Sans cette migration, la production garderait les anciennes valeurs.**

``kpi_env.get_dashboard_parameters`` résout override navire → ligne globale →
défaut codé : une ligne semée en base **l'emporte** sur le défaut du code.
Changer ``DASHBOARD_PARAM_DEFAULTS`` ne suffit donc pas pour une base déjà
seedée — c'est exactement la classe de défaut de l'incident
``DFT-20260904-001``, où un référentiel en base divergeait du code sans que rien
ne le signale.

Deux corrections, toutes deux issues de la méthodologie de performance
environnementale v3.0 :

1. **``ef_container_ship_gco2_tkm`` est supprimé** (§11.1). La relation entre la
   taille d'un navire et son facteur d'émission n'étant pas linéaire, le segment
   retenu déterminait le taux de décarbonation sur une plage de 87 % à 96 %. La
   méthodologie écarte cette base et précise : « ne pas réintroduire sans une
   nouvelle décision explicite ».

2. **``ef_airfreight_gco2_tkm`` passe de 800 à 630** gCO₂/t·km (§11.3). La
   méthodologie retient la part *Opération* seule de la Base Empreinte ADEME
   (0,63 kgCO₂e/t·km) ; 800 correspondait au total de 0,80, **amont compris**.
   On comparait donc une émission tank-to-wake à une référence well-to-wake, en
   surestimant cette dernière de 27 %.

Valeurs **en dur**, jamais importées du code applicatif : une migration est un
instantané, pas un appel au code vivant (règle du projet).

Idempotente : le ``DELETE`` et l'``UPDATE`` sont sans effet si déjà appliqués.
Les **overrides par navire** sont traités comme la ligne globale — un override
du comparateur aérien resterait sinon sur l'ancienne valeur, et un override du
porte-conteneurs survivrait à la suppression de son paramètre.

Revision ID: 20260911_0147
Revises: 20260911_0146
"""

from alembic import op

revision = "20260911_0147"
down_revision = "20260911_0146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Le porte-conteneurs disparaît — ligne globale ET overrides navire.
    op.execute(
        "DELETE FROM dashboard_parameters "
        "WHERE parameter_name = 'ef_container_ship_gco2_tkm'"
    )
    # 2. L'aérien est ramené à la part Opération seule.
    #
    # ⚠️ On ne met à jour QUE les lignes encore à 800 : une valeur déjà corrigée
    # à la main en Administration ne doit pas être écrasée par la migration.
    op.execute(
        "UPDATE dashboard_parameters SET value = 630 "
        "WHERE parameter_name = 'ef_airfreight_gco2_tkm' AND value = 800"
    )


def downgrade() -> None:
    """Rétablit les deux valeurs d'origine.

    Le porte-conteneurs est recréé **uniquement en ligne globale** : les
    éventuels overrides par navire supprimés à l'``upgrade`` ne sont pas
    reconstituables, faute d'en avoir gardé la trace. Le downgrade rend donc le
    paramètre, pas son paramétrage fin — à savoir avant de l'invoquer.
    """
    op.execute(
        "UPDATE dashboard_parameters SET value = 800 "
        "WHERE parameter_name = 'ef_airfreight_gco2_tkm' AND value = 630"
    )
    op.execute(
        "INSERT INTO dashboard_parameters (parameter_name, vessel_id, value, unit) "
        "SELECT 'ef_container_ship_gco2_tkm', NULL, 16, 'gCO2/t.km' "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM dashboard_parameters "
        "  WHERE parameter_name = 'ef_container_ship_gco2_tkm' AND vessel_id IS NULL"
        ")"
    )
