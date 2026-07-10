"""Dashboard Performance Environnementale — LOT 11 : intégration routeur.

Vérifie : routes enregistrées ; gate de permission page 1 (``kpi:C`` — 403
pour ``rh``, rôle sans entrée ``kpi`` dans la matrice) ; gate page 4
(``mrv:S`` — 403 pour ``data_analyst``, qui n'a que ``CM`` sur ``mrv`` ; OK
pour ``administrateur``) ; édition d'un ``DashboardParameter`` (valeur
changée + ``activity_log`` tracé) ; fragment HTMX (méthode B→A change
l'affichage, gabarit fragment renvoyé quand ``HX-Request`` est présent).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.finance import LegKPI
from app.models.leg import Leg
from app.models.port import Port
from app.models.validation import DashboardParameter
from app.models.vessel import Vessel
from app.permissions import require_permission
from tests.integration.conftest import FakeRequest


def _rh_user():
    """Rôle sans entrée ``kpi`` dans la matrice (cf. app/permissions.py)."""
    return SimpleNamespace(id=2, full_name="RH Test", username="rh1", role="rh")


def _data_analyst_user():
    """A ``mrv: CM`` (pas de S) — doit échouer sur la page 4."""
    return SimpleNamespace(id=3, full_name="Data Analyst", username="da1", role="data_analyst")


def _admin_user():
    return SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")


async def _seed_one_laden_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos", is_active=True))
    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="BRSSZ", name="Santos", country="BR"))
    await db.flush()
    now = datetime(2026, 3, 1, tzinfo=UTC)
    db.add(
        Leg(
            id=1,
            leg_code="1AFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=now,
            eta_ref=now,
            etd=now,
            eta=now,
            ata=now,
            distance_nm=Decimal("1000"),
        )
    )
    await db.flush()
    db.add(LegKPI(leg_id=1, tonnage_kg=Decimal("500000"), co2_emitted_kg=Decimal("50000")))
    await db.flush()


# ═══════════════════════════════════════ routes enregistrées ═══════════════════════════════════════


def test_routes_registered():
    from app.routers import dashboard_env_router

    paths = {r.path for r in dashboard_env_router.router.routes}
    assert "/dashboard-env" in paths
    assert "/dashboard-env/" in paths
    assert "/dashboard-env/parameters" in paths
    assert "/dashboard-env/parameters/{param_id}/update" in paths


# ═══════════════════════════════════════ Page 1 — gate kpi:C ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_fleet_page_requires_kpi_c(db):
    checker = require_permission("kpi", "C")

    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=_rh_user(), db=db)
    assert exc.value.status_code == 403

    admin = _admin_user()
    assert await checker(FakeRequest(), user=admin, db=db) is admin


@pytest.mark.asyncio
async def test_fleet_page_renders_200_for_authorized_role(db):
    from app.routers.dashboard_env_router import dashboard_env_fleet

    await _seed_one_laden_leg(db)
    resp = await dashboard_env_fleet(
        FakeRequest(), year=2026, method="A", vessel=None, db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_env/index.html"
    ctx = resp.context
    assert ctx["summary"].fleet.leg_count == 1
    assert ctx["method"] == "A"
    assert ctx["year"] == 2026


# ═══════════════════════════════════════ Page 4 — gate mrv:S ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_admin_page_requires_mrv_s(db):
    checker = require_permission("mrv", "S")

    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=_data_analyst_user(), db=db)
    assert exc.value.status_code == 403

    admin = _admin_user()
    assert await checker(FakeRequest(), user=admin, db=db) is admin


@pytest.mark.asyncio
async def test_admin_page_renders_200_and_lists_parameters(db):
    from app.routers.dashboard_env_router import dashboard_env_parameters
    from app.services.validation_engine import seed_reference_data

    await seed_reference_data(db)
    resp = await dashboard_env_parameters(FakeRequest(), db=db, user=_admin_user())
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_env/parameters.html"
    names = {p.parameter_name for p in resp.context["params"]}
    assert "occupancy_rate_pct" in names
    assert "ef_container_ship_gco2_tkm" in names
    assert "ef_container_ship_gco2_tkm" in resp.context["provisional_params"]


# ═══════════════════════════════════ POST édition — valeur + activity_log ═══════════════════════════


@pytest.mark.asyncio
async def test_update_parameter_changes_value_and_records_activity(db):
    from app.routers.dashboard_env_router import dashboard_env_parameters_update
    from app.services.validation_engine import seed_reference_data

    await seed_reference_data(db)
    param = (
        await db.execute(
            select(DashboardParameter).where(
                DashboardParameter.parameter_name == "ef_container_ship_gco2_tkm"
            )
        )
    ).scalar_one()
    assert param.value == Decimal("16")

    admin = _admin_user()
    resp = await dashboard_env_parameters_update(
        param.id, FakeRequest(), value="20", unit="gCO2/t.km", db=db, user=admin
    )
    assert resp.status_code == 303

    refreshed = await db.get(DashboardParameter, param.id)
    assert refreshed.value == Decimal("20")
    assert refreshed.updated_by == admin.id

    logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "dashenv_parameter_update")
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].module == "dashboard_env"
    assert logs[0].entity_type == "dashboard_parameter"
    assert logs[0].entity_id == param.id
    assert "16" in logs[0].detail and "20" in logs[0].detail


@pytest.mark.asyncio
async def test_update_parameter_rejects_invalid_value(db):
    from app.routers.dashboard_env_router import dashboard_env_parameters_update
    from app.services.validation_engine import seed_reference_data

    await seed_reference_data(db)
    param = (
        await db.execute(
            select(DashboardParameter).where(
                DashboardParameter.parameter_name == "occupancy_rate_pct"
            )
        )
    ).scalar_one()

    with pytest.raises(HTTPException) as exc:
        await dashboard_env_parameters_update(
            param.id, FakeRequest(), value="pas-un-nombre", unit="", db=db, user=_admin_user()
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_update_parameter_unknown_id_404(db):
    from app.routers.dashboard_env_router import dashboard_env_parameters_update

    with pytest.raises(HTTPException) as exc:
        await dashboard_env_parameters_update(
            999999, FakeRequest(), value="10", unit="", db=db, user=_admin_user()
        )
    assert exc.value.status_code == 404


# ═══════════════════════════════ Fragment HTMX — méthode B→A change l'affichage ═══════════════════


@pytest.mark.asyncio
async def test_htmx_request_returns_fragment_template(db):
    from app.routers.dashboard_env_router import dashboard_env_fleet

    await _seed_one_laden_leg(db)
    req = FakeRequest()
    req.headers["hx-request"] = "true"

    resp = await dashboard_env_fleet(
        req, year=2026, method="A", vessel=None, db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_env/_fleet_fragment.html"


@pytest.mark.asyncio
async def test_method_switch_b_to_a_changes_rendered_ef(db):
    """Changer le filtre méthode (B→A) sur le même endpoint change l'EF affiché
    — jamais mélangées dans le même chiffre (cf. services.kpi_env)."""
    from app.routers.dashboard_env_router import dashboard_env_fleet

    await _seed_one_laden_leg(db)
    admin = _admin_user()

    resp_b = await dashboard_env_fleet(
        FakeRequest(), year=2026, method="B", vessel=None, db=db, user=admin
    )
    resp_a = await dashboard_env_fleet(
        FakeRequest(), year=2026, method="A", vessel=None, db=db, user=admin
    )

    ef_b = resp_b.context["summary"].fleet.ef
    ef_a = resp_a.context["summary"].fleet.ef
    assert ef_b.method == "B"
    assert ef_a.method == "A"
    assert ef_a.value_gco2_tkm != ef_b.value_gco2_tkm
    assert resp_b.context["method"] == "B"
    assert resp_a.context["method"] == "A"


@pytest.mark.asyncio
async def test_unknown_method_falls_back_to_a(db):
    """Un ``method`` de query string invalide retombe sur ``A`` (jamais d'erreur 500 exposée)."""
    from app.routers.dashboard_env_router import dashboard_env_fleet

    await _seed_one_laden_leg(db)
    resp = await dashboard_env_fleet(
        FakeRequest(), year=2026, method="not-a-method", vessel=None, db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.context["method"] == "A"


# ═══════════════════════════════════════════════════════════════════════════
# LOT 12 — pages 2 (suivi opérationnel) & 3 (qualité) + drill-down + exports
# ═══════════════════════════════════════════════════════════════════════════


def _commercial_user():
    """A ``kpi:C`` mais PAS ``mrv:C`` — voit la page 2 navire, pas le détail voyage."""
    return SimpleNamespace(id=4, full_name="Commercial", username="com1", role="commercial")


def _req_csrf():
    """FakeRequest doté d'un jeton CSRF (templates avec formulaires — page 3)."""
    req = FakeRequest()
    req.state.csrf_token = "test-csrf"
    return req


async def _load_1egb5(db):
    """Voyage golden réaliste (mouillage + soutage) — fixtures lot 13."""
    from tests.fixtures.mrv_2025.loader import load_voyage

    return await load_voyage(db, "1EGB5")


# ── Page 2 — navire : gate kpi:C ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vessel_page_requires_kpi_c(db):
    checker = require_permission("kpi", "C")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=_rh_user(), db=db)
    assert exc.value.status_code == 403
    admin = _admin_user()
    assert await checker(FakeRequest(), user=admin, db=db) is admin


@pytest.mark.asyncio
async def test_vessel_page_renders_and_lists_voyages(db):
    from app.routers.dashboard_env_router import dashboard_env_vessel

    fixture = await _load_1egb5(db)
    resp = await dashboard_env_vessel(
        fixture.vessel.id, _req_csrf(), year=2025, method="A", db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_env/vessel.html"
    op = resp.context["op"]
    assert op.vessel_id == fixture.vessel.id
    assert op.leg_count >= 1
    assert any(v.leg_code == "1EGB5" for v in op.voyages)


@pytest.mark.asyncio
async def test_vessel_page_unknown_vessel_404(db):
    from app.routers.dashboard_env_router import dashboard_env_vessel

    with pytest.raises(HTTPException) as exc:
        await dashboard_env_vessel(
            999999, FakeRequest(), year=2025, method="A", db=db, user=_admin_user()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_vessel_htmx_returns_fragment(db):
    from app.routers.dashboard_env_router import dashboard_env_vessel

    fixture = await _load_1egb5(db)
    req = _req_csrf()
    req.headers["hx-request"] = "true"
    resp = await dashboard_env_vessel(
        fixture.vessel.id, req, year=2025, method="A", db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_env/_vessel_fragment.html"


# ── Page 2 — drill-down voyage : gate mrv:C ─────────────────────────────────


@pytest.mark.asyncio
async def test_voyage_detail_requires_mrv_c(db):
    checker = require_permission("mrv", "C")
    # commercial a kpi:C (voit la page navire) mais PAS mrv:C (pas le détail).
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=_commercial_user(), db=db)
    assert exc.value.status_code == 403
    admin = _admin_user()
    assert await checker(FakeRequest(), user=admin, db=db) is admin


@pytest.mark.asyncio
async def test_voyage_detail_renders(db):
    from app.routers.dashboard_env_router import dashboard_env_voyage

    fixture = await _load_1egb5(db)
    resp = await dashboard_env_voyage(fixture.leg.id, _req_csrf(), db=db, user=_admin_user())
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_env/voyage.html"
    assert resp.context["d"].leg_code == "1EGB5"
    # La géométrie ROB + les segments carte sont prêts pour l'affichage.
    assert resp.context["rob"]["has_data"] is True
    assert isinstance(resp.context["map_segments"], list)


@pytest.mark.asyncio
async def test_voyage_detail_unknown_404(db):
    from app.routers.dashboard_env_router import dashboard_env_voyage

    with pytest.raises(HTTPException) as exc:
        await dashboard_env_voyage(999999, FakeRequest(), db=db, user=_admin_user())
    assert exc.value.status_code == 404


# ── Page 3 — qualité : gate mrv:C + action confirm-reset (route LOT 8) ──────


@pytest.mark.asyncio
async def test_quality_page_requires_mrv_c(db):
    checker = require_permission("mrv", "C")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=_rh_user(), db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_quality_page_renders(db):
    from app.routers.dashboard_env_router import dashboard_env_quality

    await _load_1egb5(db)
    resp = await dashboard_env_quality(_req_csrf(), vessel_id=None, db=db, user=_admin_user())
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_env/quality.html"
    assert "o" in resp.context


@pytest.mark.asyncio
async def test_confirm_reset_from_page3_reaches_lot8_and_traces(db):
    """La page 3 liste un reset en attente et son formulaire POST atteint la
    route LOT 8 (``mrv_router``) — l'action confirme + trace (activity_log)."""
    from sqlalchemy import select

    from app.models.nav_event import NavEventEngineReading
    from app.models.vessel_env import VesselEngine
    from app.routers.dashboard_env_router import dashboard_env_quality
    from app.routers.mrv_router import mrv_qualite_confirm_reset
    from app.services.kpi_env import quality_overview

    fixture = await _load_1egb5(db)
    engine = (
        await db.execute(select(VesselEngine).where(VesselEngine.vessel_id == fixture.vessel.id))
    ).scalars().first()
    reading = NavEventEngineReading(
        event_id=fixture.events[0].id,
        engine_id=engine.id,
        fuel_counter_l=Decimal("1000"),
        is_counter_reset=True,  # posé par le bord, pas encore confirmé
    )
    db.add(reading)
    await db.flush()

    # La page 3 (quality_overview) surface le reset en attente.
    overview = await quality_overview(db)
    assert any(pr.reading_id == reading.id for pr in overview.pending_resets)

    # Rendu de la page 3 (le formulaire de confirmation pointe vers la route LOT 8).
    resp = await dashboard_env_quality(_req_csrf(), vessel_id=None, db=db, user=_admin_user())
    assert resp.status_code == 200

    # Le POST atteint la route LOT 8 : reset confirmé + tracé.
    admin = _admin_user()
    r = await mrv_qualite_confirm_reset(reading.id, FakeRequest(), db=db, user=admin)
    assert r.status_code == 303
    refreshed = await db.get(NavEventEngineReading, reading.id)
    assert refreshed.reset_confirmed_by == admin.id
    logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "mrv_counter_reset_confirm")
            )
        ).scalars().all()
    )
    assert len(logs) == 1


# ── Exports voyage — PDF / DOCX (perm mrv:C) ────────────────────────────────


@pytest.mark.asyncio
async def test_voyage_export_pdf(db):
    from app.routers.dashboard_env_router import dashboard_env_voyage_pdf

    fixture = await _load_1egb5(db)
    resp = await dashboard_env_voyage_pdf(fixture.leg.id, db=db, user=_admin_user())
    assert resp.status_code == 200
    assert resp.media_type == "application/pdf"
    assert bytes(resp.body).startswith(b"%PDF")


@pytest.mark.asyncio
async def test_voyage_export_docx(db):
    from app.routers.dashboard_env_router import dashboard_env_voyage_docx

    fixture = await _load_1egb5(db)
    resp = await dashboard_env_voyage_docx(fixture.leg.id, db=db, user=_admin_user())
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.media_type
    # Un .docx est un ZIP (signature "PK").
    assert bytes(resp.body).startswith(b"PK")


@pytest.mark.asyncio
async def test_voyage_export_pdf_unknown_404(db):
    from app.routers.dashboard_env_router import dashboard_env_voyage_pdf

    with pytest.raises(HTTPException) as exc:
        await dashboard_env_voyage_pdf(999999, db=db, user=_admin_user())
    assert exc.value.status_code == 404
