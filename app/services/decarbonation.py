"""Base de décarbonation « nous-mêmes sans voiles » — méthodologie v3.0 §11.2.

**La question à laquelle ce module répond** : « et si ces marchandises n'avaient
pas voyagé à la voile ? » La réponse retenue par l'entreprise est de comparer le
navire **à lui-même, moteur seul à pleine puissance, sur la route directe**.

🔴 **Ce qui a été écarté, et pourquoi.** La v2.2 comparait à un porte-conteneurs
conventionnel. La méthodologie v3.0 (§11.1) l'a retiré le 08/09/2026 : la
relation entre la taille d'un navire et son facteur d'émission n'étant pas
linéaire, le segment de capacité retenu déterminait le résultat sur une plage de
87 % à 96 %. Un indicateur dont la valeur dépend à ce point d'un choix
discrétionnaire n'est pas défendable sous la directive (UE) 2024/825.
**Ne pas réintroduire cette base sans une nouvelle décision explicite.**

**Ce qui rend celle-ci solide**, et c'est l'essentiel :

- le navire n'est comparé **qu'à lui-même**, sur la route réellement parcourue ;
- les deux termes sont sur le **même périmètre** — GES tank-to-wake des deux
  côtés, facteur MEPC.391(81) des deux côtés ;
- **presque aucun paramètre n'est une hypothèse interne** : puissance,
  consommation spécifique et les vitesses d'ANEMOS et d'ARTEMIS viennent des
  fichiers EEDI **visés par Bureau Veritas** (REF-07 / REF-08), opposables au
  même titre que les références réglementaires. C'est ce qui a permis de clore
  le risque G1 de l'audit le 10/09/2026.

  ⚠️ **Une réserve, et il faut la porter soi-même plutôt que se la faire
  opposer** : ATLANTIS n'a pas encore son fichier. Sa vitesse (11,86 kn) vient
  d'une **courbe de classe** ajustée sur les six points des deux navires
  documentés — le paramètre le plus pertinent pour un membre non mesuré de la
  série, mais une dérivation, pas une mesure. C'est la faiblesse **W3** de
  l'audit, ouverte pour ce seul navire, et elle se ferme à réception du
  document ;
- elle ne dépend **pas** de ``time_b2b_h``, champ qui porte des valeurs
  impossibles sur trois voyages historiques, mais de la **distance**, mesurée au
  fond depuis la trace GPS.

> **Les approximations qui restent penchent CONTRE nous**, et c'est ce qui rend
> cette base difficile à attaquer. Les vitesses d'essai sont des vitesses de
> base mesurée, en eau calme et sans marge de mer — un navire à moteur
> traversant réellement l'Atlantique tiendrait moins (la marge usuelle est de 15
> à 25 % sur la puissance). Une vitesse plus élevée **raccourcit** le voyage de
> référence, donc **abaisse** la référence et **sous-estime** notre taux. Un
> objecteur qui soulèverait ce point plaiderait pour un chiffre **supérieur** à
> celui que nous publions.

**Usage.** La méthodologie §1.2 bis est explicite : le taux de décarbonation
**n'est pas publié de notre propre initiative**. Il est suivi en interne,
documenté, et communiqué **sur demande** — en énonçant alors la base qui le
sous-tend, sans quoi le chiffre serait inintelligible. L'indicateur porté vers
l'extérieur est le **profil de propulsion** (``kpi_env``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# ════════════════════════════════════════════════ Constantes de classe (REF-07/08)

#: Puissance propulsive installée — deux moteurs de 429 kW (REF-07 § 3.1.3.2).
INSTALLED_POWER_KW = Decimal("858")

#: Consommation spécifique, fiche moteur (REF-07 § 3.1.3.1) — g/kWh.
#:
#: 🔴 On passe par la consommation SPÉCIFIQUE, pas par la colonne « average fuel
#: consumption » des essais : l'en-tête de cette colonne **ne donne pas son
#: unité**, et ses trois lignes ne sont compatibles avec les 212 g/kWh du
#: motoriste que si l'unité est le litre par heure et par moteur (216 g/kWh) —
#: les autres lectures donnent 108 à 128 g/kWh, impossible pour un diesel.
#: Passer par la consommation spécifique évite cette inférence **et donne la
#: valeur la plus basse** : 4,37 t/j contre 4,46.
SPECIFIC_FUEL_CONSUMPTION_G_PER_KWH = Decimal("212")

#: Conso journalière du scénario moteur seul, à pleine puissance (t/jour).
#: 858 kW × 212 g/kWh × 24 h ÷ 1e6 = 4,365… t/j — arrondi méthodologie : 4,37.
BASELINE_FUEL_T_PER_DAY = (
    INSTALLED_POWER_KW * SPECIFIC_FUEL_CONSUMPTION_G_PER_KWH * Decimal("24") / Decimal("1000000")
)

#: Facteur de route directe (REF-03, tables 4-1 et 4-3).
#:
#: 🔴 Un navire sans voiles **ne cherche pas le vent**. Notre routage météo
#: allonge la distance pour trouver des conditions favorables : c'est la
#: contrepartie d'un choix, pas une charge à imputer au navire de référence.
#: Bureau Veritas retient le même principe. **Ce choix nous coûte 7,7 points.**
DIRECT_ROUTE_FACTOR = Decimal("1.211")

#: Vitesse de repli quand un navire n'a pas encore sa vitesse d'essai : AUCUNE.
#:
#: Délibéré. Un repli sur la vitesse d'un sistership préjugerait auquel des deux
#: le navire ressemble, alors qu'ils diffèrent de 12 % en puissance à vitesse
#: égale. Mieux vaut une absence motivée qu'un taux fabriqué.
NA_NO_BASELINE_SPEED = "vitesse d'essai non renseignée pour ce navire (fichier EEDI attendu)"
NA_NO_DISTANCE = "distance du voyage inconnue"
NA_NO_EMISSIONS = "émissions GES du voyage non calculées"


@dataclass(frozen=True)
class DecarbonationResult:
    """Taux de décarbonation d'un voyage ou d'un périmètre.

    ``rate_pct`` est ``None`` dès qu'un terme manque — jamais 0, qui se lirait
    comme « aucune décarbonation ». ``na_reason`` dit alors lequel.
    """

    baseline_ghg_t: Decimal | None
    actual_ghg_t: Decimal | None
    avoided_ghg_t: Decimal | None
    rate_pct: Decimal | None
    na_reason: str | None


_PCT = Decimal("0.1")
_T = Decimal("0.001")


def baseline_ghg_t(
    *,
    distance_nm: Decimal | None,
    speed_kn: Decimal | None,
    ghg_factor_t_per_t: Decimal,
) -> Decimal | None:
    """Émissions GES du scénario « même navire, moteur seul, route directe ».

    ``distance_ref = distance / 1,211`` puis ``durée = distance_ref / vitesse``
    et ``référence = 4,37 t/j × durée × F_GHG`` (méthodologie §11.2).

    La durée se déduit de la **distance**, jamais d'une durée déclarée : le champ
    ``time_b2b_h`` porte des valeurs impossibles sur trois voyages historiques —
    2NZF5 y déclare 20,8 nœuds de quai à quai.
    """
    if distance_nm is None or distance_nm <= 0 or speed_kn is None or speed_kn <= 0:
        return None
    distance_ref_nm = distance_nm / DIRECT_ROUTE_FACTOR
    duration_days = distance_ref_nm / speed_kn / Decimal("24")
    return BASELINE_FUEL_T_PER_DAY * duration_days * ghg_factor_t_per_t


def rate(baseline_t: Decimal | None, actual_t: Decimal | None) -> DecarbonationResult:
    """``(référence − réel) / référence × 100`` — les deux termes en GES TtW.

    ⚠️ Les deux côtés doivent être sur le **même périmètre**. La méthodologie
    §4.3 l'impose : « une comparaison doit être établie sur le périmètre le plus
    complet disponible **des deux côtés** du rapport ». D'où le CO₂**eq**, et non
    le CO₂ seul des intensités — et d'où l'arbitrage PRG (AR5) qui a dû précéder
    ce module.
    """
    if baseline_t is None:
        return DecarbonationResult(None, actual_t, None, None, NA_NO_DISTANCE)
    if actual_t is None:
        return DecarbonationResult(baseline_t, None, None, None, NA_NO_EMISSIONS)
    if baseline_t <= 0:
        return DecarbonationResult(baseline_t, actual_t, None, None, NA_NO_DISTANCE)
    avoided = baseline_t - actual_t
    return DecarbonationResult(
        baseline_ghg_t=baseline_t.quantize(_T),
        actual_ghg_t=actual_t.quantize(_T),
        avoided_ghg_t=avoided.quantize(_T),
        rate_pct=(avoided / baseline_t * Decimal("100")).quantize(_PCT),
        na_reason=None,
    )


@dataclass(frozen=True)
class VoyageInput:
    """Un voyage, réduit à ce dont la base a besoin."""

    distance_nm: Decimal | None
    ghg_t: Decimal | None  # CO₂eq TtW du voyage, assiette Métier
    speed_kn: Decimal | None


def aggregate(inputs: list[VoyageInput], *, ghg_factor_t_per_t: Decimal) -> DecarbonationResult:
    """Taux d'un PÉRIMÈTRE — sommer les deux termes, PUIS faire le ratio.

    🔴 **Jamais une moyenne de taux** (méthodologie §11.2, dernier alinéa). Une
    moyenne arithmétique donnerait le même poids à un voyage de 800 milles et à
    un voyage de 5 000. Même règle que ``kpi_env.aggregate_ef`` pour les
    intensités et que ``combine_propulsion_profiles`` pour le profil.

    Et, comme pour les intensités : un voyage dont la référence n'est pas
    calculable — vitesse d'essai absente, distance inconnue — quitte les **deux**
    termes. Apporter ses émissions sans sa référence gonflerait le taux.
    """
    baseline_total = Decimal("0")
    actual_total = Decimal("0")
    usable = 0
    for item in inputs:
        base = baseline_ghg_t(
            distance_nm=item.distance_nm,
            speed_kn=item.speed_kn,
            ghg_factor_t_per_t=ghg_factor_t_per_t,
        )
        if base is None or item.ghg_t is None:
            continue
        baseline_total += base
        actual_total += item.ghg_t
        usable += 1
    if not usable:
        return DecarbonationResult(None, None, None, None, NA_NO_BASELINE_SPEED)
    return rate(baseline_total, actual_total)
