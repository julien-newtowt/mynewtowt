"""Base de décarbonation « nous-mêmes sans voiles » — méthodologie v3.0 §11.2.

Ces tests épinglent les chiffres **publiés par l'entreprise**, pas seulement le
comportement du code : si l'application et la méthodologie divergent, c'est ici
que ça doit se voir.
"""

from __future__ import annotations

from decimal import Decimal

from app.services import decarbonation as dc

# Facteur GES TtW du MDO (méthodologie §4.2) — celui que `emission_ledger`
# reconstitue avec les PRG AR5.
F_GHG = Decimal("3.2551")


def test_the_daily_consumption_matches_the_published_figure():
    """858 kW × 212 g/kWh × 24 h = 4,37 t/jour (méthodologie §11.2, A9).

    Aucun de ces trois nombres n'est une hypothèse interne : puissance et
    consommation spécifique viennent du fichier EEDI visé par Bureau Veritas.
    C'est ce qui a permis de clore le risque G1 de l'audit.
    """
    assert dc.BASELINE_FUEL_T_PER_DAY.quantize(Decimal("0.01")) == Decimal("4.37")


def test_each_ship_reproduces_its_published_consumption_per_mile():
    """🔴 Les trois navires, et surtout : PAS la même valeur.

    ANEMOS et ARTEMIS sont sisterships sur tout ce que les fichiers EEDI
    enregistrent, mais leurs courbes vitesse/puissance diffèrent d'environ 12 %.
    Prendre la vitesse d'ANEMOS pour toute la flotte flattait ARTEMIS de
    2,6 points de taux de décarbonation — d'où une vitesse PAR NAVIRE.

    La consommation par mille se lit sur la **route directe**, celle que suit le
    navire de référence : c'est la colonne kg/nm du tableau §11.2.
    """
    per_day_kg = dc.BASELINE_FUEL_T_PER_DAY * Decimal("1000")
    published = {
        "ANEMOS": (Decimal("11.36"), Decimal("16.0")),
        "ARTEMIS": (Decimal("12.07"), Decimal("15.1")),
        "ATLANTIS": (Decimal("11.86"), Decimal("15.3")),
    }
    for ship, (speed_kn, expected) in published.items():
        kg_per_nm = (per_day_kg / (speed_kn * Decimal("24"))).quantize(Decimal("0.1"))
        assert kg_per_nm == expected, ship


def test_the_baseline_sails_the_direct_route_not_ours():
    """Un navire sans voiles NE CHERCHE PAS LE VENT.

    Notre routage météo allonge la distance pour trouver des conditions
    favorables : c'est la contrepartie d'un choix, pas une charge à imputer au
    navire de référence. La distance de référence est donc la nôtre **divisée**
    par 1,211 — elle est plus courte, donc la référence est plus basse, donc
    notre taux est plus faible. Ce choix coûte 7,7 points à l'entreprise, et
    c'est assumé.
    """
    ours = dc.baseline_ghg_t(
        distance_nm=Decimal("5782"), speed_kn=Decimal("11.36"), ghg_factor_t_per_t=F_GHG
    )
    # La même chose sans le facteur de route (référence plus longue, donc plus
    # émettrice) : si le facteur était appliqué à l'envers, on obtiendrait ceci.
    naive = (
        dc.BASELINE_FUEL_T_PER_DAY * (Decimal("5782") / Decimal("11.36") / Decimal("24")) * F_GHG
    )
    assert ours < naive
    assert (naive / ours).quantize(Decimal("0.001")) == dc.DIRECT_ROUTE_FACTOR


def test_the_rate_is_computed_on_the_same_boundary_on_both_sides():
    """Référence et réel en GES TtW, jamais l'un en CO₂ seul et l'autre en CO₂eq.

    La méthodologie §4.3 l'impose : une comparaison se fait « sur le périmètre le
    plus complet disponible DES DEUX CÔTÉS du rapport ». C'est la raison pour
    laquelle l'arbitrage sur les PRG (AR5) devait précéder ce module.
    """
    res = dc.rate(Decimal("1000"), Decimal("450"))
    assert res.avoided_ghg_t == Decimal("550.000")
    assert res.rate_pct == Decimal("55.0")
    assert res.na_reason is None


def test_a_scope_sums_both_terms_then_takes_the_ratio():
    """🔴 Jamais une moyenne de taux (méthodologie §11.2, dernier alinéa).

    Deux voyages très inégaux. La moyenne arithmétique des taux donnerait le même
    poids au voyage de 800 milles qu'à celui de 5 000 — c'est le pendant exact de
    la règle d'agrégation des intensités.
    """
    long_leg = dc.VoyageInput(
        distance_nm=Decimal("5000"), ghg_t=Decimal("100"), speed_kn=Decimal("11.36")
    )
    short_leg = dc.VoyageInput(
        distance_nm=Decimal("800"), ghg_t=Decimal("60"), speed_kn=Decimal("11.36")
    )

    scope = dc.aggregate([long_leg, short_leg], ghg_factor_t_per_t=F_GHG)

    base_long = dc.baseline_ghg_t(
        distance_nm=Decimal("5000"), speed_kn=Decimal("11.36"), ghg_factor_t_per_t=F_GHG
    )
    base_short = dc.baseline_ghg_t(
        distance_nm=Decimal("800"), speed_kn=Decimal("11.36"), ghg_factor_t_per_t=F_GHG
    )
    expected = ((base_long + base_short - Decimal("160")) / (base_long + base_short)) * Decimal(
        "100"
    )
    assert scope.rate_pct == expected.quantize(Decimal("0.1"))

    # La moyenne des deux taux individuels donnerait autre chose — on vérifie
    # que ce n'est PAS ce que fait l'agrégat.
    r_long = dc.rate(base_long, Decimal("100")).rate_pct
    r_short = dc.rate(base_short, Decimal("60")).rate_pct
    assert scope.rate_pct != ((r_long + r_short) / 2).quantize(Decimal("0.1"))


def test_a_ship_without_a_trial_speed_leaves_both_terms():
    """Pas de vitesse d'essai ⇒ le voyage sort des DEUX termes.

    Même règle que §8.2 n°2 pour les intensités : apporter ses émissions sans sa
    référence gonflerait mécaniquement le taux. Et surtout, on ne substitue PAS
    la vitesse d'un sistership — ils diffèrent de 12 %, et un repli préjugerait
    auquel des deux le navire ressemble.
    """
    measured = dc.VoyageInput(
        distance_nm=Decimal("5000"), ghg_t=Decimal("100"), speed_kn=Decimal("11.36")
    )
    unknown = dc.VoyageInput(distance_nm=Decimal("5000"), ghg_t=Decimal("900"), speed_kn=None)

    alone = dc.aggregate([measured], ghg_factor_t_per_t=F_GHG)
    with_unknown = dc.aggregate([measured, unknown], ghg_factor_t_per_t=F_GHG)

    assert with_unknown.rate_pct == alone.rate_pct
    assert with_unknown.actual_ghg_t == Decimal("100.000")  # les 900 t sont sorties


def test_nothing_computable_yields_a_motivated_absence_not_a_zero():
    """Zéro se lirait « aucune décarbonation ». Une absence motivée, non."""
    res = dc.aggregate(
        [dc.VoyageInput(distance_nm=None, ghg_t=Decimal("10"), speed_kn=None)],
        ghg_factor_t_per_t=F_GHG,
    )
    assert res.rate_pct is None
    assert res.na_reason == dc.NA_NO_BASELINE_SPEED


def test_the_published_scope_rate_is_reproducible():
    """Reproduction du chiffre publié : 28 voyages commerciaux, 55,9 %.

    La méthodologie §11.2 publie une référence de **4 731,2 t CO₂e** contre
    **2 087,2 t** d'émissions réelles, soit 2 644,0 t évitées et **55,9 %**. On
    ne dispose pas ici des 28 voyages, mais la formule du taux doit reproduire
    ces trois nombres à partir des deux totaux — c'est ce qui garantit que
    l'application et le document diront la même chose.
    """
    res = dc.rate(Decimal("4731.2"), Decimal("2087.2"))
    assert res.avoided_ghg_t == Decimal("2644.000")
    assert res.rate_pct == Decimal("55.9")
