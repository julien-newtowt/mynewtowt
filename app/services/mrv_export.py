"""MRV — reliquat legacy conservé après le décommissionnement (LOT 14).

`SOF_TO_MRV_MAP` reste consommé par ``services.voyage_events`` (classification
départ/arrivée). C'est la seule chose encore vivante de ce module : le CSV DNV
(9 et 18 colonnes), `carbon_report_summary` et le modèle `MRVEvent` sous-jacent
ont été retirés (suppression totale du legacy MRV). Les sorties réglementaires
vivent dans ``services.mrv_dataset`` (OVDLA/OVDBR).
"""

from __future__ import annotations

SOF_TO_MRV_MAP: dict[str, str] = {
    "SOSP": "departure",  # Start of Sea Passage → MRV departure
    "EOSP": "arrival",  # End of Sea Passage → arrival
    "ANCHORED": "begin_anchoring",
    "WEIGH_ANCHOR": "end_anchoring",
    "BUNKER_START": "bunkering_start",
    "BUNKER_END": "bunkering_end",
}


def map_sof_to_mrv_type(sof_event_type: str) -> str | None:
    return SOF_TO_MRV_MAP.get(sof_event_type)
