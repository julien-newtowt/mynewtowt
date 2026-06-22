"""Contrôle de non-régression PAR RAPPORT À LA VERSION D'ORIGINE (V2).

Vérifie que les fonctionnalités RESTAURÉES dans la reprise V3 respectent le
contrat de l'ancienne version (archive `mytowt-main`), et trace explicitement
les fonctionnalités V2 PAS ENCORE reprises (``pytest.skip`` avec motif) — ce
fichier sert de tableau de bord de parité V2↔V3, vivant et exécutable.

Convention :
- ``test_v2_*`` qui ASSERTENT = contrat V2 restauré et vérifié.
- ``test_pending_*`` qui SKIPPENT = fonctionnalité V2 spécifiée mais pas encore
  implémentée (cf. docs/audit/backlog & docs/audit/specs).
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint


def _paths(router) -> set[str]:
    return {getattr(r, "path", "") for r in router.routes}


def _methods(router) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in router.routes:
        for m in getattr(r, "methods", set()) or set():
            out.add((m, getattr(r, "path", "")))
    return out


# ───────────────────────── Lot 0 — sécurité/intégrité ─────────────────────────


def test_v2_vessel_position_unique_constraint_restored():
    """V2 avait uq (vessel_id, recorded_at) ; supprimée en V3, ici restaurée."""
    from app.models.claim import VesselPosition

    ucs = [c for c in VesselPosition.__table__.constraints if isinstance(c, UniqueConstraint)]
    cols = {tuple(sorted(col.name for col in uc.columns)) for uc in ucs}
    assert ("recorded_at", "vessel_id") in cols


def test_v2_api_key_guard_restored():
    from app.routers.api_v1_router import require_api_key

    assert callable(require_api_key)


def test_v2_antijump_filter_restored():
    """V2 filtrait les sauts satcom aberrants dans la distance réelle."""
    from app.services.voyage_track import MAX_PLAUSIBLE_SPEED_KN, actual_distance_nm

    assert MAX_PLAUSIBLE_SPEED_KN > 0
    # signature étendue avec le filtre
    import inspect

    assert "max_speed_kn" in inspect.signature(actual_distance_nm).parameters


# ───────────────────────────── Cargo (V2 parité) ─────────────────────────────

# V2 PackingListBatch portait les adresses structurées + description marchandise.
_V2_BATCH_FIELDS = (
    "shipper_name", "shipper_address", "shipper_postal", "shipper_city", "shipper_country",
    "notify_name", "notify_address", "notify_postal", "notify_city", "notify_country",
    "consignee_name", "consignee_address", "consignee_postal", "consignee_city",
    "consignee_country", "type_of_goods", "description_of_goods",
)


@pytest.mark.parametrize("field", _V2_BATCH_FIELDS)
def test_v2_packing_batch_fields_restored(field):
    from app.models.packing_list import PackingListBatch

    assert hasattr(PackingListBatch, field), f"champ V2 absent : {field}"


def test_v2_bl_numbering_format_restored():
    """V2 numérotait le BL en TUAW_{voyage}_{seq}. On préserve le préfixe TUAW_."""
    import inspect

    from app.services import packing_list as pl

    assert hasattr(pl, "assign_bl_number")
    src = inspect.getsource(pl.assign_bl_number)
    assert "TUAW_" in src


def test_v2_cargo_staff_routes_restored():
    """V2 : édition/suppression batch, historique audit, BL, Arrival Notice."""
    from app.routers.cargo_packing_router import router

    p = _paths(router)
    assert "/cargo/packing-lists/{pl_id}/batches/{batch_id}/edit" in p
    assert "/cargo/packing-lists/{pl_id}/batches/{batch_id}/delete" in p
    assert "/cargo/packing-lists/{pl_id}/history" in p
    assert "/cargo/packing-lists/{pl_id}/batches/{batch_id}/bl.pdf" in p
    assert "/cargo/packing-lists/{pl_id}/arrival-notice.pdf" in p


def test_v2_cargo_portal_routes_restored():
    """V2 : édition/suppression batch + dépôt de documents côté portail token."""
    from app.routers.cargo_portal_router import router

    p = _paths(router)
    assert "/p/{token}/packing/batches/{batch_id}/edit" in p
    assert "/p/{token}/packing/batches/{batch_id}/delete" in p
    assert "/p/{token}/documents" in p
    assert "/p/{token}/documents/upload" in p
    assert "/p/{token}/documents/{doc_id}/download" in p
    assert "/p/{token}/documents/{doc_id}/delete" in p


# ───────────────────────────── Escale (V2 parité) ─────────────────────────────


def test_v2_escale_edit_delete_routes_restored():
    from app.routers.escale_router import router

    p = _paths(router)
    assert "/escale/operations/{op_id}/edit" in p
    assert "/escale/operations/{op_id}/delete" in p
    assert "/escale/dockers/{shift_id}/edit" in p
    assert "/escale/dockers/{shift_id}/delete" in p


def test_v2_docker_productivity_restored():
    from app.models.escale import DockerShift

    for prop in ("planned_rate", "actual_rate", "rate_delta_pct"):
        assert hasattr(DockerShift, prop), prop


# ───────────────────────────── Crew (V2 parité) ─────────────────────────────


def test_v2_crew_routes_restored():
    """V2 : édition fiche marin, Crew List PAF, édition/suppression affectation."""
    from app.routers.crew_router import router

    m = _methods(router)
    assert ("POST", "/crew/members/{member_id}/edit") in m  # CREW-01
    assert ("GET", "/crew/members/{member_id}/edit") in m
    assert ("GET", "/crew/border-police/{vessel_id}") in m  # CREW-02
    assert ("POST", "/crew/assignments/{assignment_id}/edit") in m  # CREW-04
    assert ("POST", "/crew/assignments/{assignment_id}/delete") in m


def test_v2_crew_member_full_fields_present():
    """CREW-03 : les champs de la fiche marin V2 existent et sont saisissables."""
    from app.models.crew import CrewMember

    for f in ("date_of_birth", "visa_us_expires_at", "visa_br_expires_at",
              "seaman_book_number", "seaman_book_expires_at", "nationality"):
        assert hasattr(CrewMember, f), f


# ───────────────────────────── MRV (V2 parité) ─────────────────────────────


def test_v2_mrv_routes_restored():
    """V2 : edit/delete event, export DNV, Carbon Report PDF, params."""
    from app.routers.mrv_router import router

    m = _methods(router)
    assert ("POST", "/mrv/events/{event_id}/edit") in m       # MRV-03
    assert ("POST", "/mrv/events/{event_id}/delete") in m
    assert ("GET", "/mrv/export/dnv.csv") in m                # MRV-01
    assert ("GET", "/mrv/export/carbon-report.pdf") in m      # MRV-02
    assert ("POST", "/mrv/params") in m                       # MRV-06


def test_v2_dnv_export_is_18_columns():
    """MRV-01 : l'export DNV Veracity a bien 18 colonnes nommées."""
    from app.services.mrv_export import DNV_18_HEADERS

    assert len(DNV_18_HEADERS) == 18
    assert DNV_18_HEADERS[0] == "IMO"


def test_v2_mrv_do_counters_present():
    """MRV-04 : les 4 compteurs DO + ME/AE/ROB calculés existent."""
    from app.models.mrv import MRVEvent

    for f in ("port_me_do_counter", "stbd_me_do_counter", "fwd_gen_do_counter",
              "aft_gen_do_counter", "me_consumption_t", "ae_consumption_t",
              "total_consumption_t", "rob_calculated_t", "lat_deg", "lat_ns"):
        assert hasattr(MRVEvent, f), f


# ──────────────────── Parité V2 NON ENCORE reprise (gaps tracés) ────────────────
# Ces fonctionnalités existaient en V2, sont spécifiées (docs/audit/specs), mais
# pas encore implémentées. Le skip documente la dette de parité de façon vivante.

_PENDING = {
    "escale_port_status_ata_atd": "ESC-02 — flux statut portuaire / pose ATA-ATD (spec écrite)",
    "crew_embark_off_leg": "CREW-04/A4 — embarquement hors leg (leg_id nullable + vessel_id)",
    "crew_ticket_upload": "CREW-05 — upload/download PJ billet (spec écrite)",
    "mrv_dms_autofill": "MRV-07 — auto-remplissage GPS de la position DMS (saisie manuelle OK)",
    "finance_forecast_actual": "FIN-01 — modèle prévisionnel/réalisé (spec écrite)",
    "finance_csv_export": "FIN-02 — export CSV finance",
    "kpi_nox_sox": "FIN-03 — NOx/SOx évités",
    "planning_commercial_pdf": "PLN-01 — brochure commerciale imprimable",
    "planning_csv_export": "PLN-03 — export CSV planning réel",
    "stowage_onboard_view": "STO-01 — vue à bord du plan de chargement",
    "stowage_drag_drop": "STO-02 — réaffectation de zone (drag-drop)",
    "onboard_sof_edit_delete": "ONB-01 — édition/suppression SOF non signé",
    "onboard_cargo_doc_structured": "ONB-02 — documents cargo structurés",
    "onboard_leg_attachments": "ONB-03 — pièces jointes leg / docs agent d'escale",
    "admin_vessel_crud": "ADM-01 — CRUD navires",
    "admin_alerts_engine": "ADM-02 — moteur d'alertes du dashboard",
    "commercial_order_leg_assign": "COM-01 — affectation commande→leg",
    "commercial_client_edit": "COM-03 — édition client",
}


@pytest.mark.parametrize("key,reason", sorted(_PENDING.items()))
def test_pending_v2_parity(key, reason):
    pytest.skip(f"Parité V2 à reprendre — {reason}")
