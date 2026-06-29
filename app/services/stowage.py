"""Stowage — plan d'arrimage 18 zones, algorithme de suggestion.

Algorithme glouton :
    - Marchandises dangereuses (IMO) ou oversized → zones SUP_AV en priorité.
    - Reste : on remplit dans ZONE_LOADING_ORDER (arrière→avant, bas→haut)
      jusqu'à atteindre la capacité de la zone, puis on passe à la suivante.

La capacité par zone dépend du format de palette (PALETTE_COEFFICIENTS dans
app.models.commercial). À défaut on prend une capacité indicative de ~50
palettes par zone (3 ponts × 6 blocs × ~50 = ~900 palettes pour un 850).

Capacité réelle (STO-07) — modèle de coefficients : la capacité d'une zone est
un nombre d'emplacements plancher EPAL-équivalents (``StowageZoneSpec``), le
format pondère via ``PALETTE_COEFFICIENTS`` et le gerbage partage l'empreinte
au sol (cf. ``epal_footprint``). Ce modèle restitue la matrice V2
(zone × format × gerbé/simple) sans table dédiée par format.
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
    BASKET_CMU_T,
    BASKET_HEIGHT_M,
    BASKET_LENGTH_CM,
    BASKET_WIDTH_CM,
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

# STO-07 — empreinte plancher d'une palette gerbée (2-high). La V2 portait une
# matrice de capacité par (zone × format × gerbé/simple) issue du xlsx ; la V3
# la restitue par un modèle de coefficients : la capacité de zone est un nombre
# d'emplacements plancher EPAL-équivalents, le format pondère via
# ``PALETTE_COEFFICIENTS``, et le gerbage partage l'empreinte au sol de la base
# (une palette gerbée ne consomme qu'une fraction d'emplacement). Approche
# documentée et centralisée dans ``epal_footprint`` ci-dessous.
STACK_FOOTPRINT_FACTOR = 0.5  # palette gerbée = ½ emplacement plancher


def epal_footprint(
    pallet_count: int | None,
    pallet_format: str | None,
    *,
    is_stacked: bool = False,
    stack_allowed: bool = True,
) -> float:
    """Empreinte plancher consommée, en palettes EPAL-équivalentes.

    Combine le coefficient de format (``PALETTE_COEFFICIENTS`` : EPAL=1,
    BARRIQUE140=2…) et le gerbage : une palette gerbée dans une zone qui
    l'autorise partage l'empreinte de sa base et ne compte que pour
    ``STACK_FOOTPRINT_FACTOR`` d'emplacement plancher. Un gerbage déclaré dans
    une zone qui ne l'autorise pas est ignoré pour l'empreinte (compté plein —
    l'incohérence est signalée par ailleurs en avertissement).
    """
    coeff = PALETTE_COEFFICIENTS.get(pallet_format or "EPAL", 1.0)
    base = (pallet_count or 0) * coeff
    if is_stacked and stack_allowed:
        return base * STACK_FOOTPRINT_FACTOR
    return base


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
        load = epal_footprint(it.get("pallet_count"), it.get("pallet_format"))
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


def batch_is_oversized(batch) -> bool:
    """Le lot dépasse le panier standard (380×150×220 cm, 5,1 t) → zone SUP_AV.

    Mêmes seuils que le gabarit panier (cf. ``app.models.stowage`` :
    ``BASKET_*``). Tolérant aux dimensions absentes (``None`` → non hors-gabarit).
    """
    if batch.length_cm and batch.length_cm > BASKET_LENGTH_CM:
        return True
    if batch.width_cm and batch.width_cm > BASKET_WIDTH_CM:
        return True
    if batch.height_cm and batch.height_cm > BASKET_HEIGHT_M * 100:
        return True
    return bool(batch.weight_kg and batch.weight_kg > BASKET_CMU_T * 1000)


def order_placeholder_item(order) -> dict:
    """STO-09 — item d'arrimage *placeholder* issu de la réservation d'une commande.

    Permet d'arrimer **avant** la création des documents cargo : quand une
    commande n'a pas encore de **batch** de packing list, on construit un item à
    partir des données de réservation (``booked_palettes``, ``palette_format``,
    poids unitaire). ``batch_id`` reste ``None`` (signal du caractère
    provisoire).

    Les caractéristiques fines (dangerosité, hors-gabarit, dimensions,
    HS/IMDG/UN) n'existent qu'au niveau batch : le placeholder est donc routé
    comme cargaison **normale** (``is_dangerous``/``is_oversized`` False), faute
    de signal au niveau commande. Re-suggérer après création des batches remplace
    le placeholder par le détail réel (et reclasse l'IMO/hors-gabarit en SUP_AV).
    """
    weight_total: float | None = None
    if order.weight_per_palette_kg is not None and order.booked_palettes:
        weight_total = float(order.weight_per_palette_kg) * order.booked_palettes
    return {
        "batch_id": None,
        "order_id": order.id,
        "pallet_format": order.palette_format or "EPAL",
        "pallet_count": order.booked_palettes or 0,
        "weight_kg": weight_total,
        "description": order.cargo_description or order.description_of_goods,
        "hs_code": None,
        "imdg_class": None,
        "un_number": None,
        "length_cm": None,
        "width_cm": None,
        "height_cm": None,
        "cubage_m3": None,
        "stackable": True,
        "is_dangerous": False,
        "is_oversized": False,
    }


async def gather_suggestion_items(db: AsyncSession, leg_id: int) -> list[dict]:
    """Items à arrimer pour un leg : batches PL des commandes, avec fallback STO-09.

    Pour chaque commande du leg :

    - si elle porte des batches de packing list → un item par batch (détail figé
      depuis la PL) ;
    - sinon (aucun batch — PL absente *ou* encore vide), si elle a des palettes
      réservées → un item *placeholder* (``order_placeholder_item``) afin
      d'autoriser l'arrimage avant la saisie des docs cargo.

    Lecture seule. Consommé par ``suggest_assignments`` (puis persistance dans le
    routeur).
    """
    from app.models.commercial import Order
    from app.models.packing_list import PackingList, PackingListBatch

    orders = list((await db.execute(select(Order).where(Order.leg_id == leg_id))).scalars().all())
    items_in: list[dict] = []
    for o in orders:
        pls = list(
            (await db.execute(select(PackingList).where(PackingList.order_id == o.id)))
            .scalars()
            .all()
        )
        batches: list = []
        for pl in pls:
            batches.extend(
                (
                    await db.execute(
                        select(PackingListBatch).where(PackingListBatch.packing_list_id == pl.id)
                    )
                )
                .scalars()
                .all()
            )
        if batches:
            for b in batches:
                # Remontée packing list → arrimage : dimension, poids, hauteur,
                # classement (HS/IMDG/UN), gerbabilité. Figés à l'affectation.
                items_in.append(
                    {
                        "batch_id": b.id,
                        "order_id": o.id,
                        "pallet_format": b.pallet_format,
                        "pallet_count": b.pallet_count,
                        "weight_kg": b.weight_kg,
                        "description": b.description,
                        "hs_code": b.hs_code,
                        "imdg_class": b.imdg_class,
                        "un_number": b.un_number,
                        "length_cm": b.length_cm,
                        "width_cm": b.width_cm,
                        "height_cm": b.height_cm,
                        "cubage_m3": b.cubage_m3,
                        "stackable": b.stackable,
                        "is_dangerous": b.hazardous,
                        "is_oversized": batch_is_oversized(b),
                    }
                )
        elif (o.booked_palettes or 0) > 0:
            # STO-09 — la commande n'a pas (encore) de packing list : placeholder.
            items_in.append(order_placeholder_item(o))
    return items_in


def zone_usage_summary(items: Iterable[dict]) -> dict[str, float]:
    """Retourne `{ zone: palettes_equivalentes }` pour visualisation."""
    used: dict[str, float] = defaultdict(float)
    for it in items:
        zone = it.get("zone")
        if not zone:
            continue
        used[zone] += epal_footprint(it.get("pallet_count"), it.get("pallet_format"))
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


# Libellés bilingues (FR/EN) des segments du code zone ``{DECK}_{HOLD}_{BLOCK}``
# pour un indice humain (cf. app.models.stowage : convention de nommage).
# STO-06 — le plan d'arrimage doit être communicable en EN (équipage / port
# étranger). Les langues non FR/EN retombent sur le FR.
_DECK_LABELS = {
    "fr": {"INF": "pont INF", "MIL": "pont MIL", "SUP": "pont SUP"},
    "en": {"INF": "lower deck", "MIL": "middle deck", "SUP": "upper deck"},
}
_HOLD_LABELS = {
    "fr": {"AR": "cale AR", "AV": "cale AV"},
    "en": {"AR": "aft hold", "AV": "fwd hold"},
}
_BLOCK_LABELS = {
    "fr": {"AR": "bloc AR", "MIL": "bloc MIL", "AV": "bloc AV"},
    "en": {"AR": "aft block", "MIL": "mid block", "AV": "fwd block"},
}


def zone_label(zone: str | None, lang: str = "fr") -> str:
    """Indice humain bilingue d'une zone ``{DECK}_{HOLD}_{BLOCK}``.

    Ex. ``INF_AR_MIL`` → ``"INF_AR_MIL — cale AR, pont INF, bloc MIL"`` (fr) /
    ``"INF_AR_MIL — aft hold, lower deck, mid block"`` (en). Renvoie le code brut
    tel quel si la zone est vide ou non conforme à la convention (3 segments) —
    on reste tolérant pour le texte libre. ``lang`` non FR/EN ⇒ FR.
    """
    if not zone:
        return ""
    parts = zone.split("_")
    if len(parts) != 3:
        return zone
    deck, hold, block = parts
    decks = _DECK_LABELS.get(lang, _DECK_LABELS["fr"])
    holds = _HOLD_LABELS.get(lang, _HOLD_LABELS["fr"])
    blocks = _BLOCK_LABELS.get(lang, _BLOCK_LABELS["fr"])
    bits = [holds.get(hold), decks.get(deck), blocks.get(block)]
    hint = ", ".join(b for b in bits if b)
    return f"{zone} — {hint}" if hint else zone


# STO-06 — libellés du PDF plan d'arrimage (FR/EN). Document remis à l'équipage
# ou au port : doit pouvoir être produit en anglais.
_PDF_LABELS: dict[str, dict] = {
    "fr": {
        "doc_kind": "Plan de chargement · Stowage",
        "vessel": "Navire",
        "leg": "Route",
        "vessel_class": "classe",
        "pallets": "Palettes",
        "loaded_tonnage": "Tonnage chargé",
        "diagram_title": "Schéma de chargement — 18 zones (poupe ◀ → ▶ proue)",
        "aft_hold": "◀ Cale ARRIÈRE",
        "fwd_hold": "Cale AVANT ▶",
        "deck": {"SUP": "Pont sup.", "MIL": "Pont interm.", "INF": "Pont inf."},
        "capacity_caption": (
            "Capacité = palettes EPAL-équivalentes du référentiel de classe · "
            "cellule = palettes / capacité · ⚠ = avertissement de zone."
        ),
        "warnings": "Avertissements",
        "assigned_lots": "Lots affectés",
        "col_zone": "Zone",
        "col_lot": "Lot",
        "col_description": "Description",
        "col_format": "Format",
        "col_pallets": "Pal.",
        "col_weight": "Poids",
        "col_class": "Classement",
        "col_stacked": "Gerbé",
        "no_assignment": "Aucune affectation.",
        "stacked": "gerbé",
        "floor": "base",
        "issued_on": "Émis le",
        "footer_note": "Plan théorique de chargement — référentiel classe",
    },
    "en": {
        "doc_kind": "Stowage plan",
        "vessel": "Vessel",
        "leg": "Leg",
        "vessel_class": "class",
        "pallets": "Pallets",
        "loaded_tonnage": "Loaded tonnage",
        "diagram_title": "Loading diagram — 18 zones (stern ◀ → ▶ bow)",
        "aft_hold": "◀ AFT hold",
        "fwd_hold": "FWD hold ▶",
        "deck": {"SUP": "Upper deck", "MIL": "Middle deck", "INF": "Lower deck"},
        "capacity_caption": (
            "Capacity = EPAL-equivalent pallets from the class reference · "
            "cell = pallets / capacity · ⚠ = zone warning."
        ),
        "warnings": "Warnings",
        "assigned_lots": "Assigned lots",
        "col_zone": "Zone",
        "col_lot": "Lot",
        "col_description": "Description",
        "col_format": "Format",
        "col_pallets": "Plts",
        "col_weight": "Weight",
        "col_class": "Class.",
        "col_stacked": "Stacked",
        "no_assignment": "No assignment.",
        "stacked": "stacked",
        "floor": "floor",
        "issued_on": "Issued on",
        "footer_note": "Theoretical loading plan — class reference",
    },
}


def stowage_pdf_labels(lang: str = "fr") -> dict:
    """Libellés du PDF plan d'arrimage pour la langue (FR/EN ; sinon FR)."""
    return _PDF_LABELS.get(lang, _PDF_LABELS["fr"])


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


async def deck_layout(db: AsyncSession, leg_id: int) -> dict[str, list[dict]]:
    """STO-10 — grille d'occupation par pont, pour la vue SVG top-down.

    Renvoie ``{deck: [zone_dict, ...]}`` pour les **3 ponts** (INF/MIL/SUP),
    chacun avec ses **6 zones** (2 cales × 3 blocs) dans l'ordre ``(hold,
    block)``. Chaque ``zone_dict`` fusionne l'occupation (palettes) et la
    capacité (specs) :

        {zone, deck, hold, block, pallet_count, capacity_epal,
         fill_ratio (0..1), is_dangerous}

    Géométrie régulière et déterministe : toutes les zones sont présentes
    (``pallet_count=0`` si vide), ce qui permet un rendu SVG sur grille fixe.
    Lecture seule (réutilise ``zones_for_leg`` + le référentiel de specs).
    """
    from app.services.stowage_specs import get_specs

    occ = {row["zone"]: row for row in await zones_for_leg(db, leg_id)}
    vessel_class = await _vessel_class_for_leg(db, leg_id)
    specs = await get_specs(db, vessel_class)

    layout: dict[str, list[dict]] = {deck: [] for deck in DECKS}
    for deck in DECKS:
        for hold in HOLDS:
            for block in BLOCKS:
                zone = f"{deck}_{hold}_{block}"
                row = occ.get(zone)
                cap = int((specs.get(zone) or {}).get("capacity_epal") or 0)
                pallets = int(row["pallet_count"]) if row else 0
                ratio = min(pallets / cap, 1.0) if cap else (1.0 if pallets else 0.0)
                layout[deck].append(
                    {
                        "zone": zone,
                        "deck": deck,
                        "hold": hold,
                        "block": block,
                        "pallet_count": pallets,
                        "capacity_epal": cap,
                        "fill_ratio": round(ratio, 3),
                        "is_dangerous": bool(
                            (row and row["is_dangerous"]) or zone in DANGEROUS_ZONES
                        ),
                    }
                )
    return layout


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
        spec = specs.get(it.zone)
        # STO-07 — empreinte pondérée format × gerbage (gerbé = ½ emplacement
        # quand la zone l'autorise).
        load_epal = epal_footprint(
            it.pallet_count,
            it.pallet_format,
            is_stacked=it.is_stacked,
            stack_allowed=bool(spec.get("stack_allowed", True)) if spec else True,
        )
        weight_t = (it.weight_kg or 0) / 1000.0
        pallet_total += it.pallet_count or 0
        used_t_total += weight_t
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


# ───────────────────── STO-05 — politique de blocage capacité (A3) ─────────────

STOWAGE_BLOCK_FLAG = "stowage_block_overcapacity"


async def check_zone_admission(
    db: AsyncSession,
    leg_id: int,
    zone: str,
    *,
    add_pallets: int,
    add_weight_kg: float | None,
    pallet_format: str | None,
) -> tuple[bool, str | None]:
    """Vérifie qu'un nouvel item tient dans la zone (capacité EPAL-éq. + poids).

    Retourne ``(ok, motif)``. ``ok=True`` si la zone n'a pas de spec (pas de
    contrainte connue) ou si l'ajout reste dans les limites.
    """
    ev = await evaluate_plan(db, leg_id)
    z = ev.get("zones", {}).get(zone)
    if z is None:
        return True, None
    new_used_epal = (z.get("used_epal") or 0.0) + epal_footprint(add_pallets, pallet_format)
    cap = z.get("capacity_epal") or 0
    if cap and new_used_epal > cap:
        return False, (
            f"Capacité dépassée en zone {zone} : " f"{new_used_epal:.0f}/{cap} pal. EPAL-éq."
        )
    max_t = z.get("max_load_t")
    if max_t and add_weight_kg:
        new_used_t = (z.get("used_t") or 0.0) + (add_weight_kg / 1000.0)
        if new_used_t > float(max_t):
            return False, (
                f"Charge maximale dépassée en zone {zone} : "
                f"{new_used_t:.1f}/{float(max_t):.1f} t."
            )
    return True, None
