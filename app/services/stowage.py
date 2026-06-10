"""Stowage — plan d'arrimage 18 zones, algorithme de suggestion.

Algorithme glouton :
    - Marchandises dangereuses (IMO) ou oversized → zones SUP_AV en priorité.
    - Reste : on remplit dans ZONE_LOADING_ORDER (arrière→avant, bas→haut)
      jusqu'à atteindre la capacité de la zone, puis on passe à la suivante.

La capacité par zone dépend du format de palette (PALETTE_COEFFICIENTS dans
app.models.commercial). À défaut on prend une capacité indicative de ~50
palettes par zone (3 ponts × 6 blocs × ~50 = ~900 palettes pour un 850).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from app.models.commercial import PALETTE_COEFFICIENTS
from app.models.stowage import (
    DANGEROUS_ZONES,
    ZONE_LOADING_ORDER,
)

ZONE_CAPACITY_DEFAULT = 50  # palettes EPAL équivalentes par zone (à affiner)


def suggest_assignments(items: Iterable[dict]) -> list[dict]:
    """items : [{ 'batch_id', 'pallet_format', 'pallet_count', 'is_dangerous', 'is_oversized' }]

    Retourne la même liste enrichie de `'zone': '<DECK>_<HOLD>_<BLOCK>'`.
    """
    items = list(items)
    used: dict[str, float] = defaultdict(float)
    out: list[dict] = []

    # 1. Dangerous / oversized → SUP_AV
    danger_queue = [it for it in items if it.get("is_dangerous") or it.get("is_oversized")]
    normal_queue = [it for it in items if it not in danger_queue]

    def _place(it: dict, candidate_zones: list[str]) -> str | None:
        coeff = PALETTE_COEFFICIENTS.get(it.get("pallet_format") or "EPAL", 1.0)
        load = (it.get("pallet_count") or 0) * coeff
        for zone in candidate_zones:
            if used[zone] + load <= ZONE_CAPACITY_DEFAULT:
                used[zone] += load
                return zone
        return None

    for it in danger_queue:
        zone = _place(it, list(DANGEROUS_ZONES))
        if zone is None:
            # fallback : autre zone si pas de place en SUP_AV
            zone = _place(it, ZONE_LOADING_ORDER)
        out.append({**it, "zone": zone or "OVERFLOW"})

    for it in normal_queue:
        # On évite SUP_AV pour les marchandises normales
        candidate = [z for z in ZONE_LOADING_ORDER if z not in DANGEROUS_ZONES]
        zone = _place(it, candidate)
        if zone is None:
            zone = _place(it, ZONE_LOADING_ORDER)
        out.append({**it, "zone": zone or "OVERFLOW"})

    return out


def zone_usage_summary(items: Iterable[dict]) -> dict[str, float]:
    """Retourne `{ zone: palettes_equivalentes }` pour visualisation."""
    used: dict[str, float] = defaultdict(float)
    for it in items:
        zone = it.get("zone")
        if not zone:
            continue
        coeff = PALETTE_COEFFICIENTS.get(it.get("pallet_format") or "EPAL", 1.0)
        used[zone] += (it.get("pallet_count") or 0) * coeff
    return dict(used)
