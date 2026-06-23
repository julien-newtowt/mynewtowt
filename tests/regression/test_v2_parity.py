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
    "shipper_name",
    "shipper_address",
    "shipper_postal",
    "shipper_city",
    "shipper_country",
    "notify_name",
    "notify_address",
    "notify_postal",
    "notify_city",
    "notify_country",
    "consignee_name",
    "consignee_address",
    "consignee_postal",
    "consignee_city",
    "consignee_country",
    "type_of_goods",
    "description_of_goods",
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


def test_v2_cargo_excel_routes_restored():
    """CARGO-09 : import/export Excel (PL, voyage, template) staff + portail."""
    from app.routers.cargo_packing_router import router as staff_router
    from app.routers.cargo_portal_router import router as portal_router
    from app.services import cargo_excel

    sp = _paths(staff_router)
    assert "/cargo/packing-lists/{pl_id}/export.xlsx" in sp
    assert "/cargo/packing-lists/{pl_id}/template.xlsx" in sp
    assert "/cargo/packing-lists/{pl_id}/import-xlsx" in sp
    assert "/cargo/packing-lists/voyage/{leg_id}/export.xlsx" in sp
    pp = _paths(portal_router)
    assert "/p/{token}/packing/template.xlsx" in pp
    assert "/p/{token}/packing/import-xlsx" in pp
    # round-trip service : export ↔ parse
    assert callable(cargo_excel.export_packing_list_xlsx)
    assert callable(cargo_excel.parse_xlsx)


def test_v2_cargo_portal_screens_restored():
    """CARGO-10/11 : écrans portail « Suivi voyage », « Guide » et fiche navire."""
    from app.routers.cargo_portal_router import router

    p = _paths(router)
    assert "/p/{token}/voyage" in p  # CARGO-10
    assert "/p/{token}/guide" in p  # CARGO-11
    assert "/p/{token}/vessel" in p  # CARGO-11


# ───────────────────────────── Escale (V2 parité) ─────────────────────────────


def test_v2_escale_edit_delete_routes_restored():
    from app.routers.escale_router import router

    p = _paths(router)
    assert "/escale/operations/{op_id}/edit" in p
    assert "/escale/operations/{op_id}/delete" in p
    assert "/escale/dockers/{shift_id}/edit" in p
    assert "/escale/dockers/{shift_id}/delete" in p
    # ESC-02 — pilotage du statut portuaire / pose ATA-ATD.
    assert "/escale/legs/{leg_id}/port-status" in p


def test_v2_docker_productivity_restored():
    from app.models.escale import DockerShift

    for prop in ("planned_rate", "actual_rate", "rate_delta_pct"):
        assert hasattr(DockerShift, prop), prop


def test_v2_escale_intervenant_durations_restored():
    """ESC-04 : intervenant + durées prévue/réelle des opérations d'escale."""
    from datetime import UTC, datetime, timedelta

    from app.models.escale import EscaleOperation

    assert hasattr(EscaleOperation, "intervenant")
    base = datetime(2026, 4, 1, tzinfo=UTC)
    op = EscaleOperation(
        leg_id=1,
        operation_type="manutention",
        action="dechargement",
        planned_start=base,
        planned_end=base + timedelta(hours=4),
    )
    assert op.planned_duration_hours == 4.0
    assert op.actual_duration_hours is None


def test_v2_escale_crew_coupling_restored():
    """ESC-06 : couplage embarquement/débarquement → équipage + auto-PAF + alertes."""
    from app.models.escale import OPERATION_ACTIONS
    from app.services.escale_crew import (
        CREW_ACTIONS,
        couple_crew_assignment,
        embarkation_alerts,
        maybe_create_paf,
    )

    assert callable(couple_crew_assignment)
    assert callable(maybe_create_paf)
    assert callable(embarkation_alerts)
    assert "embarquement" in CREW_ACTIONS and "debarquement" in CREW_ACTIONS
    assert "passage_paf" in OPERATION_ACTIONS  # action auto-PAF restaurée


def test_v2_ux_time_input_restored():
    """UX-01 : partial de saisie d'heure portuaire (fuseau UTC/Paris/Port local + aperçu UTC)."""
    from app.templating import templates

    macro = templates.env.get_template("staff/_time_input.html").module.tz_datetime
    html = str(macro("planned_start", label="Début"))
    assert 'class="tz-input-wrap"' in html and 'class="tz-select"' in html
    assert "tz-utc-hint" in html


def test_v2_ux_topbar_notif_and_lang_restored():
    """UX-04/05 : la cloche liste le vrai flux + sélecteur de langue staff."""
    from types import SimpleNamespace

    from app.templating import _BRAND_BY_LANG, templates

    ctx = {
        "request": SimpleNamespace(url=SimpleNamespace(path="/dashboard")),
        "lang": "fr",
        "brand": _BRAND_BY_LANG["fr"],
        "user": SimpleNamespace(full_name="Op", username="op", role="operation"),
        "notif_count": 1,
        "recent_notifications": [
            SimpleNamespace(title="Test", detail=None, link="/x", is_read=False)
        ],
        "lang_options": ["fr", "en", "vi"],
        "newtowt_agent_enabled": True,
    }
    html = templates.env.get_template("staff/_topbar.html").render(**ctx)
    assert "Test" in html and "/lang/en" in html and "topbar-lang-menu" in html


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

    for f in (
        "date_of_birth",
        "visa_us_expires_at",
        "visa_br_expires_at",
        "seaman_book_number",
        "seaman_book_expires_at",
        "nationality",
    ):
        assert hasattr(CrewMember, f), f


# ───────────────────────────── MRV (V2 parité) ─────────────────────────────


def test_v2_mrv_routes_restored():
    """V2 : edit/delete event, export DNV, Carbon Report PDF, params."""
    from app.routers.mrv_router import router

    m = _methods(router)
    assert ("POST", "/mrv/events/{event_id}/edit") in m  # MRV-03
    assert ("POST", "/mrv/events/{event_id}/delete") in m
    assert ("GET", "/mrv/export/dnv.csv") in m  # MRV-01
    assert ("GET", "/mrv/export/carbon-report.pdf") in m  # MRV-02
    assert ("POST", "/mrv/params") in m  # MRV-06


def test_v2_dnv_export_is_18_columns():
    """MRV-01 : l'export DNV Veracity a bien 18 colonnes nommées."""
    from app.services.mrv_export import DNV_18_HEADERS

    assert len(DNV_18_HEADERS) == 18
    assert DNV_18_HEADERS[0] == "IMO"


def test_v2_mrv_do_counters_present():
    """MRV-04 : les 4 compteurs DO + ME/AE/ROB calculés existent."""
    from app.models.mrv import MRVEvent

    for f in (
        "port_me_do_counter",
        "stbd_me_do_counter",
        "fwd_gen_do_counter",
        "aft_gen_do_counter",
        "me_consumption_t",
        "ae_consumption_t",
        "total_consumption_t",
        "rob_calculated_t",
        "lat_deg",
        "lat_ns",
    ):
        assert hasattr(MRVEvent, f), f


# ───────────────────────────── Commercial (V2 parité) ────────────────────────


def test_v2_commercial_routes_restored():
    """V2 : affectation commande→leg, édition/désactivation client."""
    from app.routers.commercial_router import router

    m = _methods(router)
    assert ("GET", "/commercial/orders/{order_id}/assign") in m  # COM-01
    assert ("POST", "/commercial/orders/{order_id}/assign") in m
    assert ("POST", "/commercial/orders/{order_id}/assignments/{assignment_id}/delete") in m
    assert ("GET", "/commercial/clients/{client_id}/edit") in m  # COM-03
    assert ("POST", "/commercial/clients/{client_id}/edit") in m
    assert ("POST", "/commercial/clients/{client_id}/toggle-active") in m


def test_v2_order_rich_fields_restored():
    """COM-02 : la commande V3 retrouve format/poids/THC/frais/route/dates + lien grille."""
    from app.models.commercial import Order

    for f in (
        "palette_format",
        "weight_per_palette_kg",
        "thc_included",
        "booking_fee",
        "documentation_fee",
        "departure_locode",
        "arrival_locode",
        "delivery_date_start",
        "delivery_date_end",
        "rate_grid_id",
        "rate_grid_line_id",
    ):
        assert hasattr(Order, f), f


# ───────────────────────────── Onboard / Captain (V2 parité) ──────────────────


def test_v2_onboard_sof_edit_delete_restored():
    """ONB-01 : édition + suppression d'un SOF non signé (+ garde lock)."""
    from app.routers.captain_router import router

    m = _methods(router)
    assert ("POST", "/captain/sof-events/{event_id}/edit") in m
    assert ("POST", "/captain/sof-events/{event_id}/delete") in m


def test_v2_onboard_leg_attachments_restored():
    """ONB-03 : upload/download/delete de pièces jointes leg + catégories."""
    from app.routers.captain_router import router

    m = _methods(router)
    assert ("POST", "/captain/legs/{leg_id}/attachments") in m
    assert ("GET", "/captain/legs/{leg_id}/attachments/{att_id}/download") in m
    assert ("POST", "/captain/legs/{leg_id}/attachments/{att_id}/delete") in m
    from app.models.leg_attachment import LEG_ATTACHMENT_CATEGORIES, LegAttachment

    assert "port_agent" in LEG_ATTACHMENT_CATEGORIES
    assert "bl_signed" in LEG_ATTACHMENT_CATEGORIES
    assert "letter_protest" in LEG_ATTACHMENT_CATEGORIES
    assert hasattr(LegAttachment, "category")


# ───────────────────────────── Finance / KPI (V2 parité) ──────────────────────


def test_v2_finance_forecast_actual_restored():
    """FIN-01 : LegFinance retrouve le couple prévisionnel/réel + écarts (A2)."""
    from app.models.finance import LegFinance

    for f in (
        "revenue_forecast_eur",
        "port_fees_forecast_eur",
        "docker_costs_forecast_eur",
        "opex_share_forecast_eur",
        "other_costs_forecast_eur",
        "margin_forecast_eur",
    ):
        assert hasattr(LegFinance, f), f
    # propriétés d'écart
    for p in ("revenue_variance_eur", "margin_variance_eur"):
        assert isinstance(getattr(LegFinance, p), property), p


def test_v2_finance_csv_export_restored():
    """FIN-02 : route d'export CSV finance prévisionnel/réel."""
    from app.routers.finance_router import router

    assert ("GET", "/finance/export/csv") in _methods(router)


def test_v2_nox_sox_avoided_restored():
    """FIN-03 : facteurs + calcul NOx/SOx évités (paramétrables)."""
    from app.services.emissions import EmissionFactors, estimate_avoided, get_emission_factors

    assert callable(estimate_avoided)
    assert callable(get_emission_factors)
    res = estimate_avoided(cargo_t=10, distance_nm=100)
    assert res.nox_avoided_kg > 0 and res.sox_avoided_kg > 0
    assert EmissionFactors is not None


# ───────────────────────────── Admin (V2 parité) ──────────────────────────────


def test_v2_vessel_crud_restored():
    """ADM-01 : CRUD navires (création/édition/désactivation)."""
    from app.routers.admin_router import router

    m = _methods(router)
    assert ("POST", "/admin/vessels") in m
    assert ("GET", "/admin/vessels/{vessel_id}/edit") in m
    assert ("POST", "/admin/vessels/{vessel_id}/edit") in m
    assert ("POST", "/admin/vessels/{vessel_id}/toggle") in m


def test_v2_dashboard_alerts_engine_restored():
    """ADM-02 : moteur d'alertes (6 familles, tri par sévérité)."""
    from app.services.dashboard_alerts import compute_alerts

    assert callable(compute_alerts)


# ───────────────────────────── Planning (V2 parité) ───────────────────────────


def test_v2_planning_exports_restored():
    """PLN-01 brochure PDF + PLN-03 export CSV du planning réel."""
    from app.routers.planning_router import router

    m = _methods(router)
    assert ("GET", "/planning/pdf/commercial") in m
    assert ("GET", "/planning/export/csv") in m


# ───────────────────────────── Stowage (V2 parité) ────────────────────────────


def test_v2_stowage_routes_restored():
    """STO-01 vue à bord + STO-02 réaffectation de zone + STO-03 retrait."""
    from app.routers.stowage_router import router

    m = _methods(router)
    assert ("GET", "/stowage/onboard/{leg_id}") in m
    assert ("POST", "/stowage/plans/{plan_id}/items/{item_id}/move") in m
    assert ("POST", "/stowage/plans/{plan_id}/items/{item_id}/delete") in m


# ──────────────── Crew / MRV / Commercial — reprise additive (V2 parité) ────────


def test_v2_crew_offleg_and_ticket_restored():
    """CREW-04 (embarquement hors leg) + CREW-05 (billet attaché)."""
    from app.models.crew import CrewAssignment
    from app.routers.crew_router import router

    assert CrewAssignment.__table__.c.leg_id.nullable is True  # A4
    assert hasattr(CrewAssignment, "vessel_id")
    for f in ("ticket_path", "ticket_filename", "ticket_mime"):
        assert hasattr(CrewAssignment, f), f
    m = _methods(router)
    assert ("POST", "/crew/assignments/{assignment_id}/ticket") in m
    assert ("GET", "/crew/assignments/{assignment_id}/ticket") in m
    assert ("POST", "/crew/assignments/{assignment_id}/ticket/delete") in m


def test_v2_mrv_dms_autofill_restored():
    """MRV-07 — convertisseur décimal→DMS + auto-remplissage de la position."""
    from app.services.mrv_compute import autofill_event_position, decimal_to_dms

    deg, minutes, hemi = decimal_to_dms(49.5, is_lat=True)
    assert (deg, hemi) == (49, "N")
    assert callable(autofill_event_position)


def test_v2_order_attachments_restored():
    """COM-04 — pièce jointe (bon de commande / contrat) sur la commande."""
    from app.models.commercial import Order
    from app.routers.commercial_router import router

    for f in ("attachment_path", "attachment_filename", "attachment_mime"):
        assert hasattr(Order, f), f
    m = _methods(router)
    assert ("POST", "/commercial/orders/{order_id}/attachment") in m
    assert ("GET", "/commercial/orders/{order_id}/attachment") in m
    assert ("POST", "/commercial/orders/{order_id}/attachment/delete") in m


# ───────────────────────────── Onboard ONB-02 (V2 parité) ─────────────────────


def test_v2_cargo_docs_guided_restored():
    """ONB-02 : 13 types de documents guidés + champs structurés + signataire."""
    from app.models.sof_event import CargoDocument
    from app.routers.captain_router import router
    from app.services.cargo_documents import CARGO_DOC_TYPES, field_defaults

    # data_json (contenu structuré) réintroduit sur le modèle.
    assert hasattr(CargoDocument, "data_json")
    # Les 13 types V2 (SOF étant géré par la section SOF dédiée) — au moins les 12 guidés.
    for code in (
        "NOR",
        "NOR_RT",
        "HOLDS_CERT",
        "KEY_MEETING",
        "PRE_MEETING",
        "MATES_RECEIPT",
        "LOP_FP",
        "LOP_DELAYS",
        "LOP_DOCUMENT",
        "LOP_QTY",
        "LOP_DEADFREIGHT",
        "LOP_OTHER",
    ):
        assert code in CARGO_DOC_TYPES, code
    # Mentions légales pré-remplies.
    assert field_defaults("LOP_FP")["reserve"]
    m = _methods(router)
    assert ("GET", "/captain/legs/{leg_id}/docs/new") in m
    assert ("GET", "/captain/legs/{leg_id}/docs/{doc_id}/edit") in m
    assert ("POST", "/captain/legs/{leg_id}/docs/{doc_id}/edit") in m


# ───────────────────────────── Tracking (V2 parité — P1) ──────────────────────


def test_v2_tracking_latest_and_import_batch_restored():
    """TRK-01 endpoint /latest + TRK-05 traçabilité d'import des positions."""
    from app.models.claim import VesselPosition
    from app.routers.tracking_router import router

    assert ("GET", "/api/tracking/latest") in _methods(router)
    for f in ("import_batch", "created_at"):
        assert hasattr(VesselPosition, f), f


def test_v2_planning_delay_and_by_port_restored():
    """PLN-05 détection de retard (≥4 h) + PLN-06 vue par port."""
    from app.routers.planning_router import router
    from app.services.planning import is_delayed, leg_delay_hours

    assert callable(is_delayed) and callable(leg_delay_hours)
    assert ("GET", "/planning/by-port") in _methods(router)


def test_v2_co2_equivalences_restored():
    """FIN-05 : équivalences pédagogiques CO₂ (vols / conteneurs)."""
    from app.services.co2 import co2_equivalences

    eq = co2_equivalences(1_050_000)
    assert eq["flights_paris_nyc"] > 0 and eq["containers_asia_eu"] > 0


def test_v2_order_confirm_autocreates_pl():
    """COM-09 : la confirmation d'une commande auto-crée la PL (+ notif ops)."""
    from app.services.notifications import notify_packing_list_created
    from app.services.packing_list import ensure_for_order

    assert callable(ensure_for_order)
    assert callable(notify_packing_list_created)


def test_v2_closure_reopen_and_recap_restored():
    """ONB-05 : réouverture de clôture + PDF récapitulatif + checklist."""
    from app.routers.captain_router import router
    from app.services.closure import closure_checklist, closure_recap_data

    assert callable(closure_checklist) and callable(closure_recap_data)
    m = _methods(router)
    assert ("POST", "/captain/legs/{leg_id}/closure/reopen") in m
    assert ("GET", "/captain/legs/{leg_id}/closure.pdf") in m


def test_v2_crew_api_and_deactivation_restored():
    """CREW-06 API par navire + CREW-08 désactivation marin."""
    from app.routers.crew_router import router

    m = _methods(router)
    assert ("GET", "/crew/api/by-vessel/{vessel_id}") in m
    assert ("POST", "/crew/members/{member_id}/toggle-active") in m


def test_v2_stowage_block_policy_restored():
    """STO-05 (A3) : politique de blocage capacité configurable (feature flag)."""
    from app.services.stowage import STOWAGE_BLOCK_FLAG, check_zone_admission

    assert callable(check_zone_admission)
    assert STOWAGE_BLOCK_FLAG == "stowage_block_overcapacity"


def test_v2_exploitation_kpis_restored():
    """FIN-04 : indicateurs d'exploitation (écart planning, durée, vitesse)."""
    from app.services.exploitation import exploitation_summary, planning_deviation_hours

    assert callable(exploitation_summary) and callable(planning_deviation_hours)
    s = exploitation_summary([], None)
    assert "avg_planning_deviation_h" in s and "avg_sea_duration_days" in s


def test_v2_admin_exports_purges_restored():
    """ADM-04 : exports CSV/ZIP whitelistés + purge ciblée (whitelist)."""
    from app.routers.admin_router import router
    from app.services.admin_data import ALLOWED_PURGE_TABLES, export_global_zip, purge_table

    assert callable(export_global_zip) and callable(purge_table)
    assert "users" not in ALLOWED_PURGE_TABLES  # jamais purger les comptes
    m = _methods(router)
    assert ("GET", "/admin/export/global.zip") in m
    assert ("GET", "/admin/export/table/{table_name}.csv") in m
    assert ("POST", "/admin/database/purge") in m


def test_v2_cargo_batch_prefill_restored():
    """CARGO-08 : 1er batch de la PL pré-rempli depuis la commande (parties, volume)."""
    from app.models.commercial import Order
    from app.services.packing_list import batch_prefill_from_order

    assert callable(batch_prefill_from_order)
    vals = batch_prefill_from_order(
        Order(reference="ORD-T", client_id=1, booked_palettes=8, shipper_name="S")
    )
    assert vals["pallet_count"] == 8 and vals["shipper_name"] == "S"


def test_v2_cargo_rich_goods_fields_restored():
    """CARGO-13 : champs goods riches (colis, valeur) + dimensions dérivées."""
    from app.models.packing_list import PackingListBatch
    from app.services.packing_list import AUDITABLE_FIELDS

    for f in ("cases_quantity", "units_per_case", "cargo_value_usd"):
        assert f in AUDITABLE_FIELDS
        assert hasattr(PackingListBatch, f)
    b = PackingListBatch(
        packing_list_id=1, length_cm=100, width_cm=100, height_cm=100, weight_kg=200
    )
    assert b.surface_m2 == 1.0 and b.volume_m3 == 1.0 and b.density == 0.2


# ──────────────────── Parité V2 NON ENCORE reprise (gaps tracés) ────────────────
# ✅ Toute la parité P0 vis-à-vis de la V2 est désormais restaurée.
# Les évolutions P1/P2 restent tracées dans docs/audit/backlog/.

_PENDING: dict[str, str] = {}


@pytest.mark.parametrize("key,reason", sorted(_PENDING.items()))
def test_pending_v2_parity(key, reason):  # pragma: no cover - plus aucun gap P0
    pytest.skip(f"Parité V2 à reprendre — {reason}")
