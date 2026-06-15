"""Référentiel d'arrimage par classe de navire — capacités & résistances.

Source de vérité métier : *plan théorique de chargement café* (classe
Phoenix — Anemos & sister-ships). Le plan décrit, pour chacune des 18 zones
``{DECK}_{HOLD}_{BLOCK}`` :

- une **capacité** en palettes EPAL-équivalentes (étude Eupal, conditionnement
  de référence) ;
- une **résistance de pont** : charge admissible (t) et poids palette max ;
- des **règles de gerbage** (les palettes lourdes ne se stackent pas partout).

Ces valeurs servent de *fallback* quand aucune ligne ``StowageZoneSpec`` n'a
été saisie en admin pour la classe. La résolution ``get_specs`` privilégie
toujours la donnée DB (éditable) et complète avec le référentiel théorique.

Contraintes documentées par le plan (résistance de pont) :

- Pont **supérieur** (SUP) : le plus léger — pas de palette US 1,4 t, pas de
  gerbage des palettes portuaires chargées de sacs 70 kg.
- Pont **intermédiaire** (MIL) & **inférieur** (INF) : palettes 1,4 t admises,
  gerbage des palettes lourdes possible.

Toutes les cales sont **ségréguées** (température & humidité contrôlées) pour
le transport de denrées (café).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stowage import ZONE_LOADING_ORDER, StowageZoneSpec

logger = logging.getLogger(__name__)

DEFAULT_VESSEL_CLASS = "phoenix"

# Capacité EPAL-équivalente par zone (étude Eupal du plan théorique café).
# Clé = zone ``{DECK}_{HOLD}_{BLOCK}``. Total = 978 palettes EPAL.
PHOENIX_ZONE_CAPACITY: dict[str, int] = {
    # Cale arrière · supérieur
    "SUP_AR_AR": 68,
    "SUP_AR_MIL": 40,
    "SUP_AR_AV": 70,
    # Cale arrière · intermédiaire
    "MIL_AR_AR": 58,
    "MIL_AR_MIL": 35,
    "MIL_AR_AV": 70,
    # Cale arrière · inférieur
    "INF_AR_AR": 19,
    "INF_AR_MIL": 45,
    "INF_AR_AV": 64,
    # Cale avant · supérieur
    "SUP_AV_AR": 63,
    "SUP_AV_MIL": 41,
    "SUP_AV_AV": 66,
    # Cale avant · intermédiaire
    "MIL_AV_AR": 67,
    "MIL_AV_MIL": 34,
    "MIL_AV_AV": 64,
    # Cale avant · inférieur
    "INF_AV_AR": 65,
    "INF_AV_MIL": 50,
    "INF_AV_AV": 59,
}

# Charge max observée par cale (deck × hold) sur l'ensemble des études du plan.
# Sert de plafond de résistance, réparti sur les blocs au prorata de la capacité.
_CALE_MAX_LOAD_T: dict[str, float] = {
    "SUP_AR": 168.0,
    "MIL_AR": 182.0,
    "INF_AR": 147.0,
    "SUP_AV": 163.2,
    "MIL_AV": 171.8,
    "INF_AV": 189.0,
}

# Résistance par pont (deck) : poids palette max admis + gerbage des lourds.
_DECK_RESISTANCE: dict[str, dict] = {
    "SUP": {"max_pallet_weight_kg": 1200.0, "heavy_stack_allowed": False},
    "MIL": {"max_pallet_weight_kg": 1400.0, "heavy_stack_allowed": True},
    "INF": {"max_pallet_weight_kg": 1400.0, "heavy_stack_allowed": True},
}

# Seuil « palette lourde » (sacs 70 kg portuaires, big bags…). Au-delà, le
# gerbage n'est admis que là où ``heavy_stack_allowed`` est vrai.
HEAVY_PALLET_KG = 900.0


def _cale_capacity(deck: str, hold: str) -> int:
    return sum(
        cap for zone, cap in PHOENIX_ZONE_CAPACITY.items() if zone.startswith(f"{deck}_{hold}_")
    )


def build_reference_specs(vessel_class: str = DEFAULT_VESSEL_CLASS) -> dict[str, dict]:
    """Référentiel théorique complet (18 zones) pour une classe de navire.

    Renvoie ``{zone: {capacity_epal, max_load_t, max_pallet_weight_kg,
    stack_allowed, heavy_stack_allowed, segregated, notes}}``. Indépendant de
    la DB — c'est le *fallback* et la base du seed admin.
    """
    out: dict[str, dict] = {}
    for zone in ZONE_LOADING_ORDER:
        deck, hold, _block = zone.split("_")
        cap = PHOENIX_ZONE_CAPACITY.get(zone, 50)
        cale_key = f"{deck}_{hold}"
        cale_total = _cale_capacity(deck, hold) or 1
        cale_max = _CALE_MAX_LOAD_T.get(cale_key, 0.0)
        max_load_t = round(cale_max * cap / cale_total, 1) if cale_max else None
        res = _DECK_RESISTANCE.get(deck, {})
        out[zone] = {
            "vessel_class": vessel_class,
            "zone": zone,
            "capacity_epal": cap,
            "max_load_t": max_load_t,
            "max_pallet_weight_kg": res.get("max_pallet_weight_kg"),
            "stack_allowed": True,
            "heavy_stack_allowed": res.get("heavy_stack_allowed", True),
            "segregated": True,
            "notes": None,
        }
    return out


def _row_to_dict(row: StowageZoneSpec) -> dict:
    return {
        "vessel_class": row.vessel_class,
        "zone": row.zone,
        "capacity_epal": row.capacity_epal,
        "max_load_t": row.max_load_t,
        "max_pallet_weight_kg": row.max_pallet_weight_kg,
        "stack_allowed": row.stack_allowed,
        "heavy_stack_allowed": row.heavy_stack_allowed,
        "segregated": row.segregated,
        "notes": row.notes,
    }


async def get_specs(db: AsyncSession, vessel_class: str | None = None) -> dict[str, dict]:
    """Specs résolues par zone pour une classe : DB (override) → référentiel.

    Lecture seule. Toujours 18 zones renvoyées : les zones non saisies en DB
    retombent sur le référentiel théorique de la classe.
    """
    vessel_class = vessel_class or DEFAULT_VESSEL_CLASS
    specs = build_reference_specs(vessel_class)
    # Renforcement : si la table n'existe pas encore (migration 0037 non
    # appliquée) on dégrade proprement vers le référentiel en mémoire au lieu
    # de planter. Savepoint isolé pour ne pas polluer la transaction de requête.
    try:
        async with db.begin_nested():
            rows = (
                (
                    await db.execute(
                        select(StowageZoneSpec).where(StowageZoneSpec.vessel_class == vessel_class)
                    )
                )
                .scalars()
                .all()
            )
    except (ProgrammingError, OperationalError):
        logger.warning(
            "stowage_zone_specs indisponible — fallback sur le référentiel %s "
            "(migration 0037 non appliquée ?)",
            vessel_class,
        )
        return specs
    for row in rows:
        if row.zone in specs:
            specs[row.zone] = _row_to_dict(row)
    return specs


async def ensure_specs(db: AsyncSession, vessel_class: str | None = None) -> dict[str, dict]:
    """Seed idempotent du référentiel d'une classe en DB (si absent).

    Crée les lignes ``StowageZoneSpec`` manquantes à partir du référentiel
    théorique. N'écrase **jamais** une ligne existante (override admin). Suit
    la convention projet : ``flush`` sans ``commit`` (géré par ``get_db``).
    """
    vessel_class = vessel_class or DEFAULT_VESSEL_CLASS
    existing = {
        row.zone
        for row in (
            (
                await db.execute(
                    select(StowageZoneSpec).where(StowageZoneSpec.vessel_class == vessel_class)
                )
            )
            .scalars()
            .all()
        )
    }
    reference = build_reference_specs(vessel_class)
    created = False
    for zone, spec in reference.items():
        if zone in existing:
            continue
        db.add(StowageZoneSpec(**spec))
        created = True
    if created:
        await db.flush()
    return await get_specs(db, vessel_class)


def capacity_total(specs: dict[str, dict]) -> int:
    """Capacité EPAL-équivalente totale du navire (somme des zones)."""
    return sum(int(s.get("capacity_epal") or 0) for s in specs.values())


def max_load_total_t(specs: dict[str, dict]) -> float:
    """Résistance totale (t) du navire (somme des plafonds de zone)."""
    return round(sum(float(s.get("max_load_t") or 0) for s in specs.values()), 1)
