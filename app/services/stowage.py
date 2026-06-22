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

import logging
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import PALETTE_COEFFICIENTS
from app.models.stowage import (
    BLOCKS,
    DANGEROUS_ZONES,
    DECKS,
    HOLDS,
    ZONE_LOADING_ORDER,
    StowageItem,
    StowagePlan,
)

logger = logging.getLogger(__name__)

ZONE_CAPACITY_DEFAULT = 50  # palettes EPAL équivalentes par zone (à affiner)


def suggest_assignments(
    items: Iterable[dict], capacities: dict[str, float] | None = None
) -> list[dict]:
    """items : [{ 'batch_id', 'pallet_format', 'pallet_count', 'is_dangerous', 'is_oversized' }]

    Retourne la même liste enrichie de `'zone': '<DECK>_<HOLD>_<BLOCK>'`.

    ``capacities`` : capacité EPAL-équivalente par zone (référentiel de la
    classe de navire — cf. ``services.stowage_specs``). À défaut, capacité
    indicative plate (``ZONE_CAPACITY_DEFAULT``).
    """
    items = list(items)
    used: dict[str, float] = defaultdict(float)
    out: list[dict] = []

    def _cap(zone: str) -> float:
        # Robustesse : une zone peut exister dans ``capacities`` avec une valeur
        # ``None`` (spec sans ``capacity_epal``). ``dict.get(zone, DEFAULT)`` ne
        # remplace PAS un None explicite → on coalesce nous-mêmes pour éviter un
        # ``float(None)`` (TypeError 500 à la génération du plan).
        val = capacities.get(zone) if capacities else None
        if val is None:
            return float(ZONE_CAPACITY_DEFAULT)
        return float(val)

    # 1. Dangerous / oversized → SUP_AV
    danger_queue = [it for it in items if it.get("is_dangerous") or it.get("is_oversized")]
    normal_queue = [it for it in items if it not in danger_queue]

    def _place(it: dict, candidate_zones: list[str]) -> str | None:
        coeff = PALETTE_COEFFICIENTS.get(it.get("pallet_format") or "EPAL", 1.0)
        load = (it.get("pallet_count") or 0) * coeff
        for zone in candidate_zones:
            if used[zone] + load <= _cap(zone):
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
    out = [{**bucket, "label": zone_label(bucket["zone"])} for bucket in agg.values()]
    # Tri par ordre de chargement ; les zones inconnues (texte libre exotique)
    # passent en fin de liste.
    out.sort(key=lambda z: order.get(z["zone"], len(order)))
    return out


def parse_zone(zone: str | None) -> tuple[str | None, str | None, str | None]:
    """Décompose une zone ``{DECK}_{HOLD}_{BLOCK}`` → ``(deck, hold, block)``.

    Renvoie ``(None, None, None)`` pour une zone vide ou non conforme (texte
    libre, ``OVERFLOW``…). Utilisé par le repérage visuel (SVG) et les vues.
    """
    if not zone:
        return (None, None, None)
    parts = zone.split("_")
    if len(parts) != 3:
        return (None, None, None)
    deck, hold, block = parts
    if deck not in DECKS or hold not in HOLDS or block not in BLOCKS:
        return (None, None, None)
    return (deck, hold, block)


async def _vessel_class_for_leg(db: AsyncSession, leg_id: int) -> str:
    """Classe du navire d'un leg (pour résoudre le référentiel d'arrimage).

    Renforcement : si la colonne ``vessels.vessel_class`` n'existe pas encore
    (migration 0037 non appliquée), on dégrade proprement vers la classe par
    défaut au lieu de planter (500). Savepoint isolé pour ne pas polluer la
    transaction de requête — même garde-fou que ``stowage_specs.get_specs``.
    """
    from app.models.leg import Leg
    from app.models.vessel import Vessel
    from app.services.stowage_specs import DEFAULT_VESSEL_CLASS

    leg = await db.get(Leg, leg_id)
    if leg is None or not leg.vessel_id:
        return DEFAULT_VESSEL_CLASS
    try:
        async with db.begin_nested():
            vessel = await db.get(Vessel, leg.vessel_id)
            return (vessel.vessel_class if vessel else None) or DEFAULT_VESSEL_CLASS
    except (ProgrammingError, OperationalError):
        logger.warning(
            "vessels.vessel_class indisponible — fallback classe %s "
            "(migration 0037 non appliquée ?)",
            DEFAULT_VESSEL_CLASS,
        )
        return DEFAULT_VESSEL_CLASS


def _per_pallet_weight_kg(item: StowageItem) -> float | None:
    """Poids unitaire d'une palette de l'item (total / nombre de palettes)."""
    if item.weight_kg is None or not item.pallet_count:
        return None
    return item.weight_kg / item.pallet_count


async def evaluate_plan(db: AsyncSession, leg_id: int) -> dict:
    """Évalue le plan d'arrimage d'un leg vs le référentiel de la classe.

    Politique : **avertissement seul** — aucune affectation n'est bloquée, les
    non-conformités (surcharge, résistance pont, gerbage interdit, hors-zone
    IMO/gabarit) sont remontées comme avertissements.

    Forme de retour::

        {
          "vessel_class": str,
          "zones": { zone: {capacity_epal, used_epal, pct, max_load_t, used_t,
                            load_pct, pallet_count, items, segregated,
                            warnings:[str]} },
          "warnings": [ {zone, level, message} ],   # liste à plat (panneau)
          "totals": {capacity_epal, used_epal, max_load_t, used_t, pallet_count},
        }

    Lecture seule. Tolérant : zones hors référentiel ignorées des totaux mais
    signalées. ``used_epal`` pondère par ``PALETTE_COEFFICIENTS``.
    """
    from app.services.stowage_specs import (
        HEAVY_PALLET_KG,
        capacity_total,
        get_specs,
        max_load_total_t,
    )

    vessel_class = await _vessel_class_for_leg(db, leg_id)
    specs = await get_specs(db, vessel_class)

    zones: dict[str, dict] = {
        zone: {
            "capacity_epal": int(spec.get("capacity_epal") or 0),
            "used_epal": 0.0,
            "pct": 0.0,
            "max_load_t": spec.get("max_load_t"),
            "used_t": 0.0,
            "load_pct": 0.0,
            "pallet_count": 0,
            "items": 0,
            "segregated": bool(spec.get("segregated")),
            "warnings": [],
        }
        for zone, spec in specs.items()
    }
    flat_warnings: list[dict] = []
    used_t_total = 0.0
    pallet_total = 0

    plan_id = (
        await db.execute(select(StowagePlan.id).where(StowagePlan.leg_id == leg_id))
    ).scalar_one_or_none()
    items: list[StowageItem] = []
    if plan_id is not None:
        # Renforcement : les colonnes packing-list de stowage_items (description,
        # hs_code, dimensions, gerbage…) sont ajoutées par la migration 0037. Si
        # elle n'est pas appliquée, le SELECT ORM échoue — on dégrade vers un
        # plan sans item (savepoint isolé) plutôt que de renvoyer un 500.
        try:
            async with db.begin_nested():
                items = list(
                    (await db.execute(select(StowageItem).where(StowageItem.plan_id == plan_id)))
                    .scalars()
                    .all()
                )
        except (ProgrammingError, OperationalError):
            logger.warning(
                "stowage_items (colonnes 0037) indisponible — plan évalué sans "
                "item (migration 0037 non appliquée ?)"
            )
            items = []

    for it in items:
        coeff = PALETTE_COEFFICIENTS.get(it.pallet_format or "EPAL", 1.0)
        load_epal = (it.pallet_count or 0) * coeff
        weight_t = (it.weight_kg or 0) / 1000.0
        pallet_total += it.pallet_count or 0
        used_t_total += weight_t
        spec = specs.get(it.zone)
        zbucket = zones.get(it.zone)
        if zbucket is None:
            flat_warnings.append(
                {
                    "zone": it.zone,
                    "level": "warn",
                    "message": f"Zone « {it.zone} » hors référentiel de la classe {vessel_class}.",
                }
            )
            continue
        zbucket["used_epal"] += load_epal
        zbucket["used_t"] += weight_t
        zbucket["pallet_count"] += it.pallet_count or 0
        zbucket["items"] += 1

        ppw = _per_pallet_weight_kg(it)
        max_ppw = spec.get("max_pallet_weight_kg") if spec else None
        # Résistance : palette trop lourde pour ce pont.
        if ppw is not None and max_ppw and ppw > max_ppw:
            msg = (
                f"Palette {ppw / 1000:.2f} t > résistance pont "
                f"({max_ppw / 1000:.2f} t) en {it.zone}."
            )
            zbucket["warnings"].append(msg)
            flat_warnings.append({"zone": it.zone, "level": "warn", "message": msg})
        # Gerbage des palettes lourdes interdit sur certains ponts.
        if (
            it.is_stacked
            and ppw is not None
            and ppw >= HEAVY_PALLET_KG
            and spec
            and not spec.get("heavy_stack_allowed", True)
        ):
            msg = f"Gerbage palette lourde non admis en {it.zone} (résistance pont)."
            zbucket["warnings"].append(msg)
            flat_warnings.append({"zone": it.zone, "level": "warn", "message": msg})
        # Gerbage d'un lot non gerbable.
        if it.is_stacked and not it.stackable:
            msg = f"Lot non gerbable affecté en gerbé ({it.zone})."
            zbucket["warnings"].append(msg)
            flat_warnings.append({"zone": it.zone, "level": "warn", "message": msg})
        # Hors-gabarit / IMO placés hors zones dédiées.
        if (it.is_oversized or it.is_dangerous) and it.zone not in DANGEROUS_ZONES:
            kind = "Hors-gabarit" if it.is_oversized else "IMO/dangereux"
            msg = f"{kind} hors zone dédiée (SUP_AV) : {it.zone}."
            zbucket["warnings"].append(msg)
            flat_warnings.append({"zone": it.zone, "level": "info", "message": msg})

    # Dépassements capacité / charge par zone.
    for zone, z in zones.items():
        if z["capacity_epal"]:
            z["pct"] = round(100 * z["used_epal"] / z["capacity_epal"], 1)
            if z["used_epal"] > z["capacity_epal"]:
                msg = (
                    f"Capacité dépassée en {zone} : "
                    f"{z['used_epal']:.0f}/{z['capacity_epal']} pal. EPAL-éq."
                )
                z["warnings"].append(msg)
                flat_warnings.append({"zone": zone, "level": "warn", "message": msg})
        if z["max_load_t"]:
            z["load_pct"] = round(100 * z["used_t"] / z["max_load_t"], 1)
            if z["used_t"] > z["max_load_t"]:
                msg = f"Surcharge en {zone} : {z['used_t']:.1f}/{z['max_load_t']:.1f} t."
                z["warnings"].append(msg)
                flat_warnings.append({"zone": zone, "level": "warn", "message": msg})

    return {
        "vessel_class": vessel_class,
        "zones": zones,
        "warnings": flat_warnings,
        "totals": {
            "capacity_epal": capacity_total(specs),
            "used_epal": round(sum(z["used_epal"] for z in zones.values()), 1),
            "max_load_t": max_load_total_t(specs),
            "used_t": round(used_t_total, 1),
            "pallet_count": pallet_total,
        },
    }


async def locate_batch(db: AsyncSession, batch_id: int) -> list[dict]:
    """Localise un lot (packing list batch) dans le navire.

    Renvoie la (ou les) position(s) d'arrimage du batch :

        [{"zone", "label", "deck", "hold", "block", "pallet_count",
          "is_dangerous", "is_oversized", "is_stacked", "leg_id",
          "plan_id", "plan_status"}]

    Liste vide si le lot n'est affecté à aucun plan. Lecture seule. C'est le
    socle du repérage visuel « où est ma marchandise à bord ? », accessible
    depuis toute vue qui positionne une cargaison.
    """
    rows = (
        await db.execute(
            select(StowageItem, StowagePlan)
            .join(StowagePlan, StowageItem.plan_id == StowagePlan.id)
            .where(StowageItem.batch_id == batch_id)
        )
    ).all()
    out: list[dict] = []
    for it, plan in rows:
        deck, hold, block = parse_zone(it.zone)
        out.append(
            {
                "zone": it.zone,
                "label": zone_label(it.zone),
                "deck": deck,
                "hold": hold,
                "block": block,
                "pallet_count": it.pallet_count or 0,
                "is_dangerous": it.is_dangerous,
                "is_oversized": it.is_oversized,
                "is_stacked": it.is_stacked,
                "leg_id": plan.leg_id,
                "plan_id": plan.id,
                "plan_status": plan.status,
            }
        )
    order = {zone: i for i, zone in enumerate(ZONE_LOADING_ORDER)}
    out.sort(key=lambda z: order.get(z["zone"], len(order)))
    return out


async def locate_for_packing_list(db: AsyncSession, packing_list_id: int) -> list[dict]:
    """Localise les lots d'une packing list dans le navire (agrégé par zone).

    Pour le repérage côté client / portail expéditeur. Renvoie uniquement les
    positions des lots de **cette** packing list — jamais l'occupation globale
    du navire (confidentialité inter-clients). Lecture seule.
    """
    from app.models.packing_list import PackingListBatch

    batch_ids = list(
        (
            await db.execute(
                select(PackingListBatch.id).where(
                    PackingListBatch.packing_list_id == packing_list_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not batch_ids:
        return []
    rows = (
        await db.execute(
            select(StowageItem, StowagePlan)
            .join(StowagePlan, StowageItem.plan_id == StowagePlan.id)
            .where(StowageItem.batch_id.in_(batch_ids))
        )
    ).all()
    agg: dict[str, dict] = {}
    for it, plan in rows:
        deck, hold, block = parse_zone(it.zone)
        bucket = agg.setdefault(
            it.zone,
            {
                "zone": it.zone,
                "label": zone_label(it.zone),
                "deck": deck,
                "hold": hold,
                "block": block,
                "pallet_count": 0,
                "is_dangerous": False,
                "leg_id": plan.leg_id,
            },
        )
        bucket["pallet_count"] += it.pallet_count or 0
        if it.is_dangerous or it.is_oversized or it.zone in DANGEROUS_ZONES:
            bucket["is_dangerous"] = True
    order = {zone: i for i, zone in enumerate(ZONE_LOADING_ORDER)}
    out = list(agg.values())
    out.sort(key=lambda z: order.get(z["zone"], len(order)))
    return out


async def locate_for_order(db: AsyncSession, order_id: int) -> list[dict]:
    """Localise les lots d'une commande dans le navire (agrégé par zone).

    Renvoie ``[{"zone", "label", "deck", "hold", "block", "pallet_count",
    "is_dangerous", "leg_id"}]`` pour la commande. Lecture seule.
    """
    rows = (
        await db.execute(
            select(StowageItem, StowagePlan)
            .join(StowagePlan, StowageItem.plan_id == StowagePlan.id)
            .where(StowageItem.order_id == order_id)
        )
    ).all()
    agg: dict[str, dict] = {}
    for it, plan in rows:
        deck, hold, block = parse_zone(it.zone)
        bucket = agg.setdefault(
            it.zone,
            {
                "zone": it.zone,
                "label": zone_label(it.zone),
                "deck": deck,
                "hold": hold,
                "block": block,
                "pallet_count": 0,
                "is_dangerous": False,
                "leg_id": plan.leg_id,
            },
        )
        bucket["pallet_count"] += it.pallet_count or 0
        if it.is_dangerous or it.is_oversized or it.zone in DANGEROUS_ZONES:
            bucket["is_dangerous"] = True
    order = {zone: i for i, zone in enumerate(ZONE_LOADING_ORDER)}
    out = list(agg.values())
    out.sort(key=lambda z: order.get(z["zone"], len(order)))
    return out
