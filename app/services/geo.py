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
        "FR", "GB", "IE", "DE", "ES", "PT", "IT", "NL", "BE", "LU",
        "NO", "SE", "DK", "FI", "IS", "PL", "CZ", "SK", "HU", "SI",
        "HR", "RO", "BG", "GR", "CY", "MT", "EE", "LV", "LT", "AT",
        "CH", "RS", "BA", "ME", "MK", "AL", "XK", "UA", "MD", "BY",
        "MC", "AD", "SM", "LI", "VA", "GI", "FO",
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
