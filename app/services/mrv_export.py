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


# NB (lot 10) : ``to_dnv_csv`` (export DNV 9 colonnes) était du CODE MORT
# (attributs ``vessel_imo``/``leg_code``/``event_type``/``consumed_t`` inexistants
# sur ``MRVEvent``) — SUPPRIMÉ. Les sorties réglementaires vivent désormais dans
# ``services.mrv_dataset`` (OVDLA/OVDBR). ``dnv_csv_18`` reste (déprécié derrière
# le flag ``mrv_v2_exports``, retrait prévu lot 14, Q3).


# MRV-01 — export DNV Veracity : 18 colonnes exactes attendues par l'ingestion.
DNV_18_HEADERS = [
    "IMO",
    "DateTime_UTC",
    "Voyage_From",
    "Voyage_To",
    "Event",
    "Time_Since_Previous_h",
    "Distance_NM",
    "Cargo_mt",
    "ME_Consumption_MDO_mt",
    "AE_Consumption_MDO_mt",
    "Total_Consumption_MDO_mt",
    "MDO_ROB_mt",
    "Latitude_deg",
    "Latitude_min",
    "Latitude_NS",
    "Longitude_deg",
    "Longitude_min",
    "Longitude_EW",
]


def _num(v, prec: int = 3) -> str:
    return f"{float(v):.{prec}f}" if v is not None else ""


def build_dnv_rows(events, *, leg_map, vessel_map, port_map) -> list[dict]:
    """Construit les lignes DNV 18 colonnes (résolues navire/leg/ports).

    ``Time_Since_Previous_h`` est calculé par navire (écart au précédent event
    du même navire). Les maps sont indexées par id.
    """
    rows: list[dict] = []
    last_dt_by_vessel: dict[int, object] = {}
    for ev in events:
        leg = leg_map.get(ev.leg_id)
        vessel = vessel_map.get(leg.vessel_id) if leg is not None else None
        pol = port_map.get(leg.departure_port_id) if leg is not None else None
        pod = port_map.get(leg.arrival_port_id) if leg is not None else None
        vid = vessel.id if vessel is not None else None
        time_since = ""
        if vid is not None and vid in last_dt_by_vessel and ev.recorded_at is not None:
            delta_h = (ev.recorded_at - last_dt_by_vessel[vid]).total_seconds() / 3600.0
            time_since = f"{delta_h:.2f}"
        if vid is not None and ev.recorded_at is not None:
            last_dt_by_vessel[vid] = ev.recorded_at
        rows.append(
            {
                "IMO": (vessel.imo_number if vessel and vessel.imo_number else ""),
                "DateTime_UTC": (ev.recorded_at.isoformat() if ev.recorded_at else ""),
                "Voyage_From": (pol.locode if pol else ""),
                "Voyage_To": (pod.locode if pod else ""),
                "Event": ev.event_kind or "",
                "Time_Since_Previous_h": time_since,
                "Distance_NM": _num(ev.distance_nm, 2),
                "Cargo_mt": _num(ev.cargo_carried_t, 2),
                "ME_Consumption_MDO_mt": _num(ev.me_consumption_t, 3),
                "AE_Consumption_MDO_mt": _num(ev.ae_consumption_t, 3),
                "Total_Consumption_MDO_mt": _num(ev.total_consumption_t, 3),
                "MDO_ROB_mt": _num(ev.rob_calculated_t, 3),
                "Latitude_deg": (ev.lat_deg if ev.lat_deg is not None else ""),
                "Latitude_min": _num(ev.lat_min, 3),
                "Latitude_NS": ev.lat_ns or "",
                "Longitude_deg": (ev.lon_deg if ev.lon_deg is not None else ""),
                "Longitude_min": _num(ev.lon_min, 3),
                "Longitude_EW": ev.lon_ew or "",
            }
        )
    return rows


def dnv_csv_18(rows: list[dict]) -> str:
    """Sérialise les lignes DNV 18 colonnes (séparateur virgule)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=DNV_18_HEADERS)
    writer.writeheader()
    for r in rows:
        writer.writerow({h: r.get(h, "") for h in DNV_18_HEADERS})
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
