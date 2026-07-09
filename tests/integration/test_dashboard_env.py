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
