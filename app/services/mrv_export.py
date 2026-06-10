"""MRV — exports DNV-CSV et Carbon Report.

`SOF_TO_MRV_MAP` indique pour chaque type d'événement SOF s'il génère
automatiquement un MRVEvent côté carburant.

CO₂ factor MDO/MGO standard : 3.206 t CO₂ / t fuel (réglementation UE).
"""

from __future__ import annotations

import csv
import io
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


def to_dnv_csv(events: Iterable[MRVEvent]) -> str:
    """Generate a DNV-compatible CSV blob from MRVEvent rows.

    Columns are the minimum subset accepted by the DNV MRV ingestion UI.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "vessel_imo",
            "leg_code",
            "event_type",
            "occurred_at_utc",
            "fuel_type",
            "rob_t",
            "consumed_t",
            "co2_t",
            "notes",
        ]
    )
    for ev in events:
        co2 = (ev.consumed_t or 0) * CO2_EMISSION_FACTOR_MDO
        writer.writerow(
            [
                getattr(ev, "vessel_imo", "") or "",
                getattr(ev, "leg_code", "") or "",
                getattr(ev, "event_type", "") or "",
                (ev.occurred_at.isoformat() if getattr(ev, "occurred_at", None) else ""),
                getattr(ev, "fuel_type", "MDO") or "MDO",
                f"{ev.rob_t:.3f}" if getattr(ev, "rob_t", None) is not None else "",
                f"{ev.consumed_t:.3f}" if getattr(ev, "consumed_t", None) is not None else "",
                f"{co2:.3f}",
                (getattr(ev, "notes", "") or "").replace("\n", " ").replace(";", ","),
            ]
        )
    return buf.getvalue()


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
