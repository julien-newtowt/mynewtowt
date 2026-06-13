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
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import PALETTE_COEFFICIENTS
from app.models.stowage import (
    DANGEROUS_ZONES,
    HOLDS,
    ZONE_LOADING_ORDER,
    StowageItem,
    StowagePlan,
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


def _hold_of(zone: str | None) -> str | None:
    """Cale (HOLD) d'une zone ``{DECK}_{HOLD}_{BLOCK}`` — segment du milieu.

    Ex. ``INF_AR_MIL`` → ``AR``. Renvoie ``None`` si la zone est vide ou
    mal formée (p. ex. ``OVERFLOW``), ou si le hold extrait n'est pas un
    HOLD connu.
    """
    if not zone:
        return None
    parts = zone.split("_")
    if len(parts) < 2:
        return None
    hold = parts[1]
    return hold if hold in HOLDS else None


async def occupation_by_hold(db: AsyncSession, leg_id: int) -> dict[str, dict]:
    """Occupation du plan d'arrimage agrégée par cale (HOLD) pour un leg.

    Relie escale (shifts dockers) ↔ stowage : pour chaque cale connue
    (``HOLDS`` = "AR"/"AV"), somme les palettes, le poids, le nombre de
    zones occupées et le nombre de zones dangereuses du plan d'arrimage du
    leg. Le hold est extrait de ``StowageItem.zone`` (segment du milieu).

    Forme de retour::

        {
            "AR": {"pallet_count": int, "weight_kg": Decimal,
                   "zones": int, "dangerous": int},
            "AV": {...},
        }

    Défensif : si aucun plan / aucun item, renvoie des zéros pour tous les
    HOLDS connus. Lecture seule (une requête, aucune écriture).
    """
    out: dict[str, dict] = {
        hold: {
            "pallet_count": 0,
            "weight_kg": Decimal("0"),
            "zones": 0,
            "dangerous": 0,
        }
        for hold in HOLDS
    }
    # Zones distinctes (et zones dangereuses) rencontrées par cale.
    seen_zones: dict[str, set[str]] = {hold: set() for hold in HOLDS}
    seen_dangerous: dict[str, set[str]] = {hold: set() for hold in HOLDS}

    plan_id = (
        await db.execute(select(StowagePlan.id).where(StowagePlan.leg_id == leg_id))
    ).scalar_one_or_none()
    if plan_id is None:
        return out

    items = (
        (await db.execute(select(StowageItem).where(StowageItem.plan_id == plan_id)))
        .scalars()
        .all()
    )
    for it in items:
        hold = _hold_of(it.zone)
        if hold is None:
            continue
        bucket = out[hold]
        bucket["pallet_count"] += it.pallet_count or 0
        if it.weight_kg is not None:
            bucket["weight_kg"] += Decimal(str(it.weight_kg))
        seen_zones[hold].add(it.zone)
        if it.is_dangerous or it.is_oversized or it.zone in DANGEROUS_ZONES:
            seen_dangerous[hold].add(it.zone)

    for hold in HOLDS:
        out[hold]["zones"] = len(seen_zones[hold])
        out[hold]["dangerous"] = len(seen_dangerous[hold])
    return out


# Libellés des segments du code zone ``{DECK}_{HOLD}_{BLOCK}`` pour un
# indice humain (cf. app.models.stowage : convention de nommage).
_DECK_LABELS = {"INF": "pont INF", "MIL": "pont MIL", "SUP": "pont SUP"}
_HOLD_LABELS = {"AR": "cale AR", "AV": "cale AV"}
_BLOCK_LABELS = {"AR": "bloc AR", "MIL": "bloc MIL", "AV": "bloc AV"}


def zone_label(zone: str | None) -> str:
    """Indice humain d'une zone ``{DECK}_{HOLD}_{BLOCK}``.

    Ex. ``INF_AR_MIL`` → ``"INF_AR_MIL — cale AR, pont INF, bloc MIL"``.
    Renvoie le code brut tel quel si la zone est vide ou non conforme à la
    convention (3 segments) — on reste tolérant pour le texte libre.
    """
    if not zone:
        return ""
    parts = zone.split("_")
    if len(parts) != 3:
        return zone
    deck, hold, block = parts
    bits = [
        _HOLD_LABELS.get(hold),
        _DECK_LABELS.get(deck),
        _BLOCK_LABELS.get(block),
    ]
    hint = ", ".join(b for b in bits if b)
    return f"{zone} — {hint}" if hint else zone


async def zones_for_leg(db: AsyncSession, leg_id: int) -> list[dict]:
    """Zones occupées du plan d'arrimage d'un leg, pour le picker claims.

    Relie un claim cargo ↔ stowage : liste les zones effectivement
    occupées par le plan d'arrimage du leg afin que l'opérateur sélectionne
    la position cale du lot sinistré (``Claim.cargo_position``).

    Forme de retour, ordonnée par ``ZONE_LOADING_ORDER`` :

        [{"zone": str, "pallet_count": int, "is_dangerous": bool,
          "label": str}, ...]

    Une zone n'apparaît qu'une fois : ``pallet_count`` somme tous les items
    de la zone, ``is_dangerous`` est vrai si un item de la zone est dangereux
    / hors-gabarit ou si la zone est une ``DANGEROUS_ZONES``. ``label`` est
    l'indice humain (cf. ``zone_label``). Liste vide si aucun plan / aucun
    item. Lecture seule (une requête, aucune écriture).
    """
    plan_id = (
        await db.execute(select(StowagePlan.id).where(StowagePlan.leg_id == leg_id))
    ).scalar_one_or_none()
    if plan_id is None:
        return []

    items = (
        (await db.execute(select(StowageItem).where(StowageItem.plan_id == plan_id)))
        .scalars()
        .all()
    )

    agg: dict[str, dict] = {}
    for it in items:
        if not it.zone:
            continue
        bucket = agg.setdefault(
            it.zone,
            {"zone": it.zone, "pallet_count": 0, "is_dangerous": False},
        )
        bucket["pallet_count"] += it.pallet_count or 0
        if it.is_dangerous or it.is_oversized or it.zone in DANGEROUS_ZONES:
            bucket["is_dangerous"] = True

    order = {zone: i for i, zone in enumerate(ZONE_LOADING_ORDER)}
    out = [
        {**bucket, "label": zone_label(bucket["zone"])}
        for bucket in agg.values()
    ]
    # Tri par ordre de chargement ; les zones inconnues (texte libre exotique)
    # passent en fin de liste.
    out.sort(key=lambda z: order.get(z["zone"], len(order)))
    return out
