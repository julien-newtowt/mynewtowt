"""MRV — reliquat legacy conservé après le décommissionnement (LOT 14).

`SOF_TO_MRV_MAP` reste consommé par ``services.voyage_events`` (classification
départ/arrivée). ``carbon_report_summary`` (agrégat fuel+CO₂) et les constantes
sont conservés (référencés par la sentinelle facteurs). Le CSV DNV (9 et 18
colonnes) a été **retiré** (Q3) : les sorties réglementaires vivent dans
``services.mrv_dataset`` (OVDLA/OVDBR).

CO₂ factor MDO/MGO standard : 3.206 t CO₂ / t fuel (réglementation UE).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.mrv import MRVEvent

SOF_TO_MRV_MAP: dict[str, str] = {
    "SOSP": "departure",  # Start of Sea Passage → MRV departure
    "EOSP": "arrival",  # End of Sea Passage → arrival
    "ANCHORED": "begin_anchoring",
    "WEIGH_ANCHOR": "end_anchoring",
    "BUNKER_START": "bunkering_start",
    "BUNKER_END": "bunkering_end",
}

CO2_EMISSION_FACTOR_MDO = 3.206
AVG_MDO_DENSITY_T_M3 = 0.845
MDO_ADMISSIBLE_DEVIATION_T = 2.0


def map_sof_to_mrv_type(sof_event_type: str) -> str | None:
    return SOF_TO_MRV_MAP.get(sof_event_type)


# LOT 14 — ``to_dnv_csv`` (9 col., code mort) puis ``dnv_csv_18`` / ``build_dnv_rows``
# / ``DNV_18_HEADERS`` (18 col.) ont été RETIRÉS (Q3). Voie unique : OVDLA/OVDBR
# (``services.mrv_dataset``).


def carbon_report_summary(events: Iterable[MRVEvent]) -> dict:
    """Compute total fuel + CO₂ over a list of MRVEvents.

    Returns: { 'total_fuel_t', 'total_co2_t', 'event_count' }.
    """
    total_fuel = 0.0
    count = 0
    for ev in events:
        if getattr(ev, "consumed_t", None) is not None:
            total_fuel += float(ev.consumed_t)
            count += 1
    return {
        "total_fuel_t": round(total_fuel, 3),
        "total_co2_t": round(total_fuel * CO2_EMISSION_FACTOR_MDO, 3),
        "event_count": count,
    }
