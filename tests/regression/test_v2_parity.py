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
    """ESC-05 : cadence dockers (pal/h) — propriétés + affichage UI."""
    from pathlib import Path

    from app.models.escale import DockerShift
    from app.templating import templates

    for prop in ("planned_rate", "actual_rate", "rate_delta_pct"):
        assert hasattr(DockerShift, prop), prop
    # La cadence est exposée dans l'écran escale (pas seulement calculée).
    tpl = Path("app/templates/staff/escale/index.html").read_text(encoding="utf-8")
    assert "Cadence (pal/h)" in tpl
    assert "s.actual_rate" in tpl and "s.rate_delta_pct" in tpl
    assert templates.env.get_template("staff/escale/index.html") is not None


def test_v2_escale_timezone_inputs_restored():
    """ESC-07 : sélecteur de fuseau (UTC/Paris/Port) sur tous les datetime escale."""
    from pathlib import Path

    from app.templating import templates

    tpl = Path("app/templates/staff/escale/index.html").read_text(encoding="utf-8")
    # Plus aucun datetime-local brut : tous passent par la macro tz_datetime.
    assert 'type="datetime-local"' not in tpl
    assert 'tz_datetime("status_time"' in tpl
    assert templates.env.get_template("staff/escale/index.html") is not None


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


def test_v2_crew_ticket_escale_coherence_restored():
    """CREW-07 : alertes billet/escale surfacées sur la fiche marin."""
    from pathlib import Path

    from app.services.escale_crew import crew_assignment_alerts

    assert callable(crew_assignment_alerts)
    # La fiche marin affiche les alertes par affectation (colonne dédiée).
    tpl = Path("app/templates/staff/crew/detail.html").read_text(encoding="utf-8")
    assert "assignment_alerts" in tpl


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


def test_v2_ux_sidebar_port_clock_restored():
    """UX-03 : horloge sidebar (UTC + port de destination) rebranchée."""
    from pathlib import Path

    layout = Path("app/templates/staff/_layout.html").read_text(encoding="utf-8")
    assert 'class="sidebar-clock"' in layout
    assert 'data-clock="utc"' in layout
    assert "next_port_tz" in layout  # liaison au port de destination
    clock_js = Path("app/static/js/clock.js").read_text(encoding="utf-8")
    assert "sidebar-clock" in clock_js and "data-clock=port" in clock_js


def test_v2_ux_vietnamese_catalog_parity_restored():
    """UX-02 : le catalogue vietnamien a la parité de clés avec le français."""
    import re

    from app.i18n import fr, vi

    fr_keys = set(fr.CATALOG)
    vi_keys = set(vi.CATALOG)
    assert fr_keys == vi_keys, f"écart de clés vi↔fr : manquant={fr_keys - vi_keys}"
    assert len(vi.CATALOG) >= 500
    # Les marqueurs de format ({count}, {name}…) sont préservés clé à clé.
    ph = re.compile(r"\{[a-zA-Z0-9_]*\}")
    mismatches = [
        k for k in fr.CATALOG if set(ph.findall(fr.CATALOG[k])) != set(ph.findall(vi.CATALOG[k]))
    ]
    assert not mismatches, f"placeholders divergents : {mismatches}"


def test_v2_cargo_portal_multilingual_restored():
    """CARGO-12 : portail expéditeur multilingue (5 langues + sélecteur)."""
    import re
    from pathlib import Path

    from app.i18n import en, es, fr, pt_br, vi
    from app.templating import templates

    # Bloc de clés portail (préfixe pt_) présent dans le catalogue de référence.
    pt_keys = {k for k in fr.CATALOG if k.startswith("pt_")}
    assert len(pt_keys) >= 150, f"trop peu de clés portail : {len(pt_keys)}"

    # Parité des clés pt_ sur les 5 langues (fr en es pt-br vi).
    for name, mod in (("en", en), ("es", es), ("pt_br", pt_br), ("vi", vi)):
        mod_pt = {k for k in mod.CATALOG if k.startswith("pt_")}
        assert pt_keys == mod_pt, f"écart de clés portail {name}↔fr : {pt_keys ^ mod_pt}"

    # Placeholders ({ref}, {count}…) préservés clé à clé dans chaque langue.
    ph = re.compile(r"\{[a-zA-Z0-9_]*\}")
    for name, mod in (("en", en), ("es", es), ("pt_br", pt_br), ("vi", vi)):
        bad = [
            k for k in pt_keys if set(ph.findall(fr.CATALOG[k])) != set(ph.findall(mod.CATALOG[k]))
        ]
        assert not bad, f"placeholders divergents {name} : {bad}"

    # Traduction réellement câblée (fr ≠ en sur un libellé de navigation).
    assert fr.CATALOG["pt_nav_overview"] != en.CATALOG["pt_nav_overview"]

    # Les templates portail utilisent t() et compilent ; sélecteur de langue présent.
    portal = Path("app/templates/portal")
    layout = (portal / "_layout.html").read_text(encoding="utf-8")
    assert "/lang/" in layout  # sélecteur de langue (liens /lang/{l})
    for name in (
        "_layout.html",
        "home.html",
        "voyage.html",
        "packing.html",
        "guide.html",
        "messages.html",
        "documents.html",
        "vessel.html",
        "privacy.html",
    ):
        assert 't("pt_' in (portal / name).read_text(encoding="utf-8"), name
        assert templates.env.get_template(f"portal/{name}") is not None


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


def test_v2_pipedrive_deal_push_restored():
    """COM-06 : push d'un Deal Pipedrive sur offre/commande (best-effort, no-op si off)."""
    import inspect

    from app.routers import commercial_router
    from app.services.pipedrive_sync import push_deal_for

    assert callable(push_deal_for)
    # Câblé dans le routeur commercial (offre émise + commande confirmée).
    src = inspect.getsource(commercial_router)
    assert "_push_pipedrive_deal" in src


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


def test_v2_mrv_multirule_quality_restored():
    """MRV-05 : contrôle qualité multi-règles + statut `error` bloquant."""
    from decimal import Decimal

    from app.models.mrv import MRVEvent
    from app.services.mrv_compute import leg_has_quality_errors, validate_event

    assert callable(leg_has_quality_errors)
    # Règle compteurs monotones : une baisse vs l'événement précédent ⇒ error.
    prev = MRVEvent(
        leg_id=1,
        event_kind="noon_consumption",
        fuel_type="MDO",
        port_me_do_counter=100,
        stbd_me_do_counter=100,
        fwd_gen_do_counter=50,
        aft_gen_do_counter=50,
    )
    cur = MRVEvent(
        leg_id=1,
        event_kind="noon_consumption",
        fuel_type="MDO",
        port_me_do_counter=90,  # en baisse → anomalie
        stbd_me_do_counter=100,
        fwd_gen_do_counter=50,
        aft_gen_do_counter=50,
    )
    validate_event(cur, prev, density=Decimal("0.845"), deviation=Decimal("2"))
    assert cur.quality_status == "error"
    assert "baisse" in (cur.quality_notes or "")
    # Le blocage qualité (export Carbon Report) s'appuie sur ce statut error.
    from pathlib import Path

    mrv_src = Path("app/routers/mrv_router.py").read_text(encoding="utf-8")
    assert 'quality_status == "error"' in mrv_src


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
    assert ("GET", "/commercial/api/rate-lookup") in m  # COM-07 — devis grille live


def test_v2_commercial_grid_performance_restored():
    """COM-08 : performance/conversion par grille + CA (tableau de bord)."""
    from app.services.commercial_dashboard import commercial_totals, grid_performance

    assert callable(grid_performance) and callable(commercial_totals)


def test_v2_commercial_editable_conversion_restored():
    """COM-05 : conversion offre→commande éditable (route/qty/format/prix)."""
    from app.routers.commercial_router import router

    m = _methods(router)
    assert ("GET", "/commercial/offers/{offer_id}/convert") in m  # écran éditable
    assert ("POST", "/commercial/offers/{offer_id}/convert") in m


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


def test_v2_onboard_notifications_inpage_restored():
    """ONB-07 : alertes de bord in-page + masquage (centre de notifications)."""
    from app.routers.captain_router import router

    m = _methods(router)
    assert ("POST", "/captain/notifications/{notif_id}/dismiss") in m


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


def test_v2_dashboard_kpis_restored():
    """ADM-03 : KPI métier (CA prévisionnel, CO₂ évité, remplissage, départs)."""
    from app.services.dashboard_kpis import (
        ca_previsionnel,
        fleet_kpis,
        upcoming_departures,
    )

    assert callable(ca_previsionnel)
    assert callable(fleet_kpis)
    assert callable(upcoming_departures)


def test_v2_admin_user_import_restored():
    """ADM-05 : import en masse d'utilisateurs (Excel) + modèle + rapport."""
    from app.routers.admin_router import router
    from app.services.user_import import build_template_xlsx, import_users, parse_users_xlsx

    assert callable(build_template_xlsx) and callable(parse_users_xlsx) and callable(import_users)
    m = _methods(router)
    assert ("GET", "/admin/users/import") in m
    assert ("GET", "/admin/users/import/template.xlsx") in m
    assert ("POST", "/admin/users/import") in m


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


def test_v2_tracking_status_color_markers_restored():
    """TRK-04 : marqueurs flotte colorés par statut (SOG) + légende."""
    from pathlib import Path

    from app.templating import templates

    js = Path("app/static/js/fleet-map.js").read_text(encoding="utf-8")
    assert "vesselStatus" in js
    # les 3 couleurs de statut (à quai / manœuvre / en mer)
    for color in ("#0D5966", "#B47148", "#87BD29"):
        assert color in js
    tpl = Path("app/templates/staff/tracking/index.html").read_text(encoding="utf-8")
    assert '"sog"' in tpl  # le SOG est transmis aux marqueurs
    assert templates.env.get_template("staff/tracking/index.html") is not None


def test_v2_navigation_avg_speed_elongation_restored():
    """TRK-03 : vitesse moyenne + allongement réel affichés en navigation."""
    from pathlib import Path

    from app.services.voyage_track import TrackMetrics

    assert hasattr(TrackMetrics, "real_elongation")
    nav = Path("app/templates/staff/navigation/index.html").read_text(encoding="utf-8")
    assert "m.avg_speed_kn" in nav and "m.real_elongation" in nav
    # le template compile (en-tête + lignes cohérentes)
    from app.templating import templates

    assert templates.env.get_template("staff/navigation/index.html") is not None


def test_v2_navigation_annual_kpis_restored():
    """TRK-02 : vue KPI navigation agrégée par année (tous legs à GPS)."""
    from pathlib import Path

    from app.routers.navigation_router import router
    from app.services.voyage_track import annual_navigation_kpis, sog_stats
    from app.templating import templates

    # endpoint agrégé annuel restauré
    assert ("GET", "/performance/navigation/kpis") in _methods(router)
    assert callable(annual_navigation_kpis) and callable(sog_stats)
    # le template compile et porte les colonnes attendues (points / SOG / distances)
    kpis = Path("app/templates/staff/navigation/kpis.html").read_text(encoding="utf-8")
    for token in ("point_count", "avg_sog_kn", "max_sog_kn", "actual_nm", "real_elongation"):
        assert token in kpis, token
    assert templates.env.get_template("staff/navigation/kpis.html") is not None


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


def test_v2_planning_share_recipient_lang_restored():
    """PLN-04 : fiche destinataire + langue + sélection leg-à-leg des partages."""
    from app.models.planning_share import PlanningShare
    from app.services.planning import parse_legs_ids

    for field in ("recipient_name", "recipient_company", "recipient_email", "lang", "legs_ids"):
        assert hasattr(PlanningShare, field), field
    assert parse_legs_ids("3,1,1") == "1,3"


def test_v2_kpi_consolidated_view_restored():
    """FIN-07 : page KPI consolidée agrégeant les sources (Commerce/Flotte/Env/Exploitation)."""
    from app.routers.kpi_router import router
    from app.services.kpi_consolidated import consolidated_kpis
    from app.templating import templates

    assert ("GET", "/kpi/consolidated") in _methods(router)
    assert callable(consolidated_kpis)
    assert templates.env.get_template("staff/kpi/consolidated.html") is not None


def test_v2_claims_insurance_exposure_restored():
    """FIN-06 : détail provision / indemnité / franchise des sinistres au KPI.

    La V2 distinguait, dans le reporting assurance, la réserve provisionnée,
    l'indemnité réglée et la franchise (déductible contrat) — la V3 n'agrégeait
    qu'un coût plat. Le service ``claims_exposure`` restaure ce détail et est
    branché dans la vue KPI consolidée.
    """
    import inspect

    from app.services.insurance_kpi import claims_exposure
    from app.services.kpi_consolidated import consolidated_kpis

    assert callable(claims_exposure)
    # Branché dans la consolidation (section ``insurance``).
    src = inspect.getsource(consolidated_kpis)
    assert "claims_exposure" in src and '"insurance"' in src
    # Carte « Assurance & sinistres » rendue sur la page consolidée.
    from app.templating import templates

    tmpl = templates.env.loader.get_source(templates.env, "staff/kpi/consolidated.html")[0]
    assert "data.insurance.provision_total" in tmpl
    assert "data.insurance.franchise_total" in tmpl
    assert "data.insurance.net_company_total" in tmpl


def test_v2_mrv_editable_params_drive_quality():
    """MRV-06 : densité MDO + seuil de déviation éditables (UI) pilotent la qualité."""
    from app.routers.mrv_router import router
    from app.services.mrv_compute import resolve_density, resolve_deviation

    # UI d'édition des paramètres MRV présente (form GET + save POST).
    m = _methods(router)
    assert ("GET", "/mrv/params") in m and ("POST", "/mrv/params") in m
    # Résolveurs canoniques des paramètres éditables (densité + seuil déviation).
    assert callable(resolve_density) and callable(resolve_deviation)
    # Le contrôle qualité du chemin de sync s'appuie bien sur ces résolveurs
    # (le comportement « seuil éditable → statut » est couvert en intégration).


def test_v2_stowage_bilingual_plan_restored():
    """STO-06 : bilinguisme FR/EN du plan d'arrimage (labels zones + PDF)."""
    from app.routers.stowage_router import router
    from app.services.stowage import stowage_pdf_labels, zone_label
    from app.templating import templates

    # Labels zones bilingues.
    assert "aft hold" in zone_label("INF_AR_AR", "en")
    assert "cale AR" in zone_label("INF_AR_AR", "fr")
    # Jeu de libellés PDF FR/EN à clés identiques.
    fr, en = stowage_pdf_labels("fr"), stowage_pdf_labels("en")
    assert set(fr.keys()) == set(en.keys()) and fr != en
    # La route PDF accepte ?lang= (param) et le template compile.
    assert ("GET", "/stowage/legs/{leg_id}/plan.pdf") in _methods(router)
    assert templates.env.get_template("pdf/stowage_plan.html") is not None


def test_v2_stowage_imdg_referential_restored():
    """STO-08 : référentiel IMDG bilingue (select IMO labellisé) restauré."""
    from pathlib import Path

    from app.services.imdg import IMDG_CLASSES, imdg_label

    codes = [c["code"] for c in IMDG_CLASSES]
    # Couverture des 9 classes + divisions officielles (≥ 20 entrées).
    assert len(IMDG_CLASSES) >= 20
    for code in ("1.1", "2.3", "3", "4.3", "5.2", "6.1", "9"):
        assert code in codes, code
    # Libellés bilingues + format « code — libellé ».
    assert imdg_label("3", "fr").startswith("3 — Liquides")
    assert imdg_label("3", "en").startswith("3 — Flammable")
    assert imdg_label("", "fr") == "" and imdg_label("999", "fr") == "999"
    # Le select stowage est alimenté par le référentiel (plus de codes en dur).
    tpl = Path("app/templates/staff/stowage/plan.html").read_text(encoding="utf-8")
    assert "for c in imdg_classes" in tpl


def test_v2_stowage_before_cargo_doc_restored():
    """STO-09 : arrimage avant cargo doc (fallback order→item placeholder)."""
    from decimal import Decimal
    from types import SimpleNamespace

    from app.services.stowage import (
        batch_is_oversized,
        gather_suggestion_items,
        order_placeholder_item,
    )

    assert callable(gather_suggestion_items) and callable(batch_is_oversized)
    # Le placeholder est construit depuis la réservation, sans batch_id.
    o = SimpleNamespace(
        id=7,
        booked_palettes=10,
        palette_format="EPAL",
        weight_per_palette_kg=Decimal("250"),
        cargo_description="Cacao",
        description_of_goods=None,
    )
    item = order_placeholder_item(o)
    assert item["batch_id"] is None  # provisoire : pas de batch source
    assert item["order_id"] == 7 and item["pallet_count"] == 10
    assert item["weight_kg"] == 2500.0
    # Routé comme cargaison normale faute de signal au niveau commande.
    assert item["is_dangerous"] is False and item["is_oversized"] is False


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


def test_v2_admin_emission_factors_editable_restored():
    """ADM-06 : facteurs NOx / SOx réexposés dans l'éditeur CO₂ versionné admin.

    En V2 les paramètres d'émission étaient éditables par l'admin/data ; en V3
    seuls les 2 facteurs CO₂ l'étaient — les 4 facteurs NOx/SOx (lus par
    ``services.emissions``) restaient invisibles. On vérifie qu'ils sont de
    nouveau dans ``CO2_VARIABLE_DEFS`` (donc rendus par le même formulaire) et
    que les routes d'édition versionnée existent.
    """
    from app.routers.admin_router import CO2_VARIABLE_DEFS, router
    from app.services import emissions as em

    for name in (em.NOX_CONV_VAR, em.NOX_SAIL_VAR, em.SOX_CONV_VAR, em.SOX_SAIL_VAR):
        assert name in CO2_VARIABLE_DEFS
    m = _methods(router)
    assert ("GET", "/admin/co2") in m
    assert ("POST", "/admin/co2/update") in m
    assert ("POST", "/admin/co2/init") in m


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
