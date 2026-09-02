"""Classification géographique des traversées (Europe ⇄ hors Europe).

Sert à colorer le Gantt par catégorie commerciale :
  - **export** : départ Europe → arrivée hors Europe (cargo quittant l'Europe) ;
  - **import** : départ hors Europe → arrivée Europe (cargo entrant en Europe) ;
  - **hors_europe** : départ ET arrivée hors Europe ;
  - **intra_eu** : départ ET arrivée en Europe (cabotage européen).

Le périmètre « Europe » est défini par codes pays ISO-3166-1 alpha-2.
"""

from __future__ import annotations

# Europe géographique (UE + AELE + Royaume-Uni + Balkans + micro-États + Est).
EUROPE_ISO2: frozenset[str] = frozenset(
    {
        "FR",
        "GB",
        "IE",
        "DE",
        "ES",
        "PT",
        "IT",
        "NL",
        "BE",
        "LU",
        "NO",
        "SE",
        "DK",
        "FI",
        "IS",
        "PL",
        "CZ",
        "SK",
        "HU",
        "SI",
        "HR",
        "RO",
        "BG",
        "GR",
        "CY",
        "MT",
        "EE",
        "LV",
        "LT",
        "AT",
        "CH",
        "RS",
        "BA",
        "ME",
        "MK",
        "AL",
        "XK",
        "UA",
        "MD",
        "BY",
        "MC",
        "AD",
        "SM",
        "LI",
        "VA",
        "GI",
        "FO",
    }
)

# Catégories de traversée (ordre figé pour la légende).
TRADE_CATEGORIES: tuple[str, ...] = ("export", "import", "hors_europe", "intra_eu")

# Libellés humains des catégories.
TRADE_CATEGORY_LABELS: dict[str, str] = {
    "export": "Export",
    "import": "Import Amérique du sud",
    "hors_europe": "Hors Europe",
    "intra_eu": "Intra-Europe",
}


def is_european(country: str | None) -> bool:
    """True si le code pays ISO-2 appartient au périmètre Europe."""
    return bool(country) and country.strip().upper() in EUROPE_ISO2


def leg_trade_category(pol_country: str | None, pod_country: str | None) -> str:
    """Catégorie d'une traversée selon les pays de départ (POL) et d'arrivée (POD)."""
    pol_eu = is_european(pol_country)
    pod_eu = is_european(pod_country)
    if pol_eu and not pod_eu:
        return "export"
    if not pol_eu and pod_eu:
        return "import"
    if not pol_eu and not pod_eu:
        return "hors_europe"
    return "intra_eu"


# ---------------------------------------------------------------------------
# Régions géographiques — cascade Zone → Pays → Port du formulaire de leg
# ---------------------------------------------------------------------------

REGION_ORDER: tuple[str, ...] = (
    "Europe",
    "Afrique",
    "Amériques",
    "Asie",
    "Océanie",
    "Antarctique",
    "Haute mer",
)
"""Ordre d'affichage des zones (Europe en tête : c'est la base de la flotte)."""

UNKNOWN_REGION = "Autre"
"""Repli pour un code pays inconnu — ne doit rester vide qu'en théorie."""

# Couverture ISO-3166-1 alpha-2 **complète**, plus les codes réservés que
# UN/LOCODE utilise réellement (``XK`` Kosovo, ``XZ`` installations en eaux
# internationales).
#
# ⚠ « Europe » ici est une **région géographique**, à ne pas confondre avec
# ``EUROPE_ISO2``, qui définit le **périmètre commercial** Europe des
# catégories import/export (et exclut délibérément la Russie et la Turquie).
# La sentinelle ``tests/unit/test_geo_regions.py`` vérifie que le périmètre
# commercial reste un sous-ensemble de la région, pour qu'ils ne divergent pas.
# La Russie et la Turquie sont rattachées à l'Europe : leurs ports de commerce
# principaux (Baltique, mer Noire, Méditerranée) y sont, et un opérateur
# européen les y cherche.
_REGION_MEMBERS: dict[str, tuple[str, ...]] = {
    "Europe": (
        # UE + AELE + Royaume-Uni + Balkans + micro-États + Est
        "AD",
        "AL",
        "AT",
        "AX",
        "BA",
        "BE",
        "BG",
        "BY",
        "CH",
        "CY",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "FO",
        "FR",
        "GB",
        "GG",
        "GI",
        "GR",
        "HR",
        "HU",
        "IE",
        "IM",
        "IS",
        "IT",
        "JE",
        "LI",
        "LT",
        "LU",
        "LV",
        "MC",
        "MD",
        "ME",
        "MK",
        "MT",
        "NL",
        "NO",
        "PL",
        "PT",
        "RO",
        "RS",
        "RU",
        "SE",
        "SI",
        "SJ",
        "SK",
        "SM",
        "TR",
        "UA",
        "VA",
        "XK",
    ),
    "Afrique": (
        "AO",
        "BF",
        "BI",
        "BJ",
        "BW",
        "CD",
        "CF",
        "CG",
        "CI",
        "CM",
        "CV",
        "DJ",
        "DZ",
        "EG",
        "EH",
        "ER",
        "ET",
        "GA",
        "GH",
        "GM",
        "GN",
        "GQ",
        "GW",
        "IO",
        "KE",
        "KM",
        "LR",
        "LS",
        "LY",
        "MA",
        "MG",
        "ML",
        "MR",
        "MU",
        "MW",
        "MZ",
        "NA",
        "NE",
        "NG",
        "RE",
        "RW",
        "SC",
        "SD",
        "SH",
        "SL",
        "SN",
        "SO",
        "SS",
        "ST",
        "SZ",
        "TD",
        "TG",
        "TN",
        "TZ",
        "UG",
        "YT",
        "ZA",
        "ZM",
        "ZW",
    ),
    "Amériques": (
        "AG",
        "AI",
        "AR",
        "AW",
        "BB",
        "BL",
        "BM",
        "BO",
        "BQ",
        "BR",
        "BS",
        "BZ",
        "CA",
        "CL",
        "CO",
        "CR",
        "CU",
        "CW",
        "DM",
        "DO",
        "EC",
        "FK",
        "GD",
        "GF",
        "GL",
        "GP",
        "GT",
        "GY",
        "HN",
        "HT",
        "JM",
        "KN",
        "KY",
        "LC",
        "MF",
        "MQ",
        "MS",
        "MX",
        "NI",
        "PA",
        "PE",
        "PM",
        "PR",
        "PY",
        "SR",
        "SV",
        "SX",
        "TC",
        "TT",
        "US",
        "UY",
        "VC",
        "VE",
        "VG",
        "VI",
    ),
    "Asie": (
        "AE",
        "AF",
        "AM",
        "AZ",
        "BD",
        "BH",
        "BN",
        "BT",
        "CC",
        "CN",
        "CX",
        "GE",
        "HK",
        "ID",
        "IL",
        "IN",
        "IQ",
        "IR",
        "JO",
        "JP",
        "KG",
        "KH",
        "KP",
        "KR",
        "KW",
        "KZ",
        "LA",
        "LB",
        "LK",
        "MM",
        "MN",
        "MO",
        "MV",
        "MY",
        "NP",
        "OM",
        "PH",
        "PK",
        "PS",
        "QA",
        "SA",
        "SG",
        "SY",
        "TH",
        "TJ",
        "TL",
        "TM",
        "TW",
        "UZ",
        "VN",
        "YE",
    ),
    "Océanie": (
        "AS",
        "AU",
        "CK",
        "FJ",
        "FM",
        "GU",
        "HM",
        "KI",
        "MH",
        "MP",
        "NC",
        "NF",
        "NR",
        "NU",
        "NZ",
        "PF",
        "PG",
        "PN",
        "PW",
        "SB",
        "TK",
        "TO",
        "TV",
        "UM",
        "VU",
        "WF",
        "WS",
    ),
    "Antarctique": ("AQ", "BV", "GS", "TF"),
    # UN/LOCODE code les installations en eaux internationales (plateformes,
    # points de transfert) sous ``XZ`` : ce ne sont pas des ports d'un pays.
    "Haute mer": ("XZ",),
}

PORT_REGIONS: dict[str, str] = {
    code: region for region, codes in _REGION_MEMBERS.items() for code in codes
}


def region_of(country: str | None) -> str:
    """Région géographique d'un code pays ISO-2 (``Autre`` si inconnu).

    Alimente le filtre **Zone** du formulaire de création de leg. La table est
    servie par l'API (``/api/v1/ports/countries``) plutôt que dupliquée dans le
    navigateur : la carte codée en dur dans ``leg-cascade.js`` ne couvrait que
    ~90 pays, et le reste du monde tombait dans « Autre ».
    """
    return PORT_REGIONS.get((country or "").strip().upper(), UNKNOWN_REGION)
