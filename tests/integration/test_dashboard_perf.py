"""Dashboard Performance Environnementale v2 — intégration routeur (NC-01/NC-04).

Vérifie : routes enregistrées ; gate de permission page 1 (``kpi:C``) ;
fragment HTMX ; repli méthode invalide → ``A`` ; et surtout le **mode
strict** — un voyage sans ``VoyageEmissionSummary(source="events")`` est
exclu des totaux (jamais mélangé en silence à une donnée legacy), tandis
qu'un voyage event-sourcé est bien compté.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.finance import LegKPI
from app.models.leg import Leg
from app.models.noon_report import NoonReport
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.permissions import require_permission
from tests.integration.conftest import FakeRequest


def _rh_user():
    """Rôle sans entrée ``kpi`` dans la matrice (cf. app/permissions.py)."""
    return SimpleNamespace(id=2, full_name="RH Test", username="rh1", role="rh")


def _admin_user():
    return SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")


async def _seed_one_laden_leg_legacy_only(db):
    """1 voyage chargé, uniquement LegKPI (aucun VoyageEmissionSummary) —
    donc ``source="legacy_kpi"`` : doit être EXCLU des totaux en mode strict."""
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


async def _seed_one_laden_leg_event_sourced(db):
    """1 voyage chargé avec un VoyageEmissionSummary(source="events") —
    doit être INCLUS dans les totaux en mode strict."""
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
    db.add(
        VoyageEmissionSummary(
            leg_id=1,
            source="events",
            co2_t=Decimal("50"),
            cargo_bl_t=Decimal("500"),
            distance_nm=Decimal("1000"),
        )
    )
    await db.flush()


# ═══════════════════════════════════════ routes enregistrées ═══════════════════════════════════════


def test_routes_registered():
    from app.routers import dashboard_perf_router

    paths = {r.path for r in dashboard_perf_router.router.routes}
    assert "/dashboard-perf" in paths
    assert "/dashboard-perf/" in paths


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
async def test_fleet_page_renders_200(db):
    from app.routers.dashboard_perf_router import dashboard_perf_fleet

    await _seed_one_laden_leg_event_sourced(db)
    resp = await dashboard_perf_fleet(
        FakeRequest(), year=2026, method="A", vessel=None, db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_perf/index.html"
    assert resp.context["method"] == "A"
    assert resp.context["year"] == 2026


@pytest.mark.asyncio
async def test_htmx_request_returns_fragment_template(db):
    from app.routers.dashboard_perf_router import dashboard_perf_fleet

    await _seed_one_laden_leg_event_sourced(db)
    req = FakeRequest()
    req.headers["hx-request"] = "true"

    resp = await dashboard_perf_fleet(
        req, year=2026, method="A", vessel=None, db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_perf/_fleet_fragment.html"


@pytest.mark.asyncio
async def test_unknown_method_falls_back_to_a(db):
    """Un ``method`` de query string invalide retombe sur ``A`` (jamais d'erreur 500 exposée)."""
    from app.routers.dashboard_perf_router import dashboard_perf_fleet

    await _seed_one_laden_leg_event_sourced(db)
    resp = await dashboard_perf_fleet(
        FakeRequest(), year=2026, method="not-a-method", vessel=None, db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.context["method"] == "A"


# ═══════════════════════════════════════ NC-04 — mode strict ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_legacy_only_leg_excluded_from_totals(db):
    """Un voyage sans VoyageEmissionSummary(source="events") n'alimente jamais
    les totaux affichés — jamais de mélange silencieux avec le repli legacy."""
    from app.routers.dashboard_perf_router import dashboard_perf_fleet

    await _seed_one_laden_leg_legacy_only(db)
    resp = await dashboard_perf_fleet(
        FakeRequest(), year=2026, method="A", vessel=None, db=db, user=_admin_user()
    )
    fleet = resp.context["summary"].fleet
    assert fleet.leg_count == 0
    assert fleet.co2_emitted_t == Decimal("0.00")
    assert fleet.legs_excluded_non_event == 1


@pytest.mark.asyncio
async def test_event_sourced_leg_included_in_totals(db):
    """Un voyage avec un résumé event-sourcé est bien compté normalement."""
    from app.routers.dashboard_perf_router import dashboard_perf_fleet

    await _seed_one_laden_leg_event_sourced(db)
    resp = await dashboard_perf_fleet(
        FakeRequest(), year=2026, method="A", vessel=None, db=db, user=_admin_user()
    )
    fleet = resp.context["summary"].fleet
    assert fleet.leg_count == 1
    assert fleet.co2_emitted_t == Decimal("50.00")
    assert fleet.legs_excluded_non_event == 0


# ═══════════════════════════════════════ Page 2 — suivi opérationnel (kpi:C) ═══════════════════════════════════════


async def _seed_two_legs_mixed_source(db):
    """2 voyages du même navire : leg 1 event-sourcé, leg 2 legacy-only (LegKPI
    seul) — pour vérifier que le mode strict n'agrège que le premier tout en
    gardant les deux dans la liste des voyages."""
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
    db.add(
        Leg(
            id=2,
            leg_code="1BBRFR6",
            vessel_id=1,
            departure_port_id=2,
            arrival_port_id=1,
            etd_ref=now,
            eta_ref=now,
            etd=now,
            eta=now,
            ata=now,
            distance_nm=Decimal("800"),
        )
    )
    await db.flush()
    db.add(
        VoyageEmissionSummary(
            leg_id=1,
            source="events",
            co2_t=Decimal("50"),
            cargo_bl_t=Decimal("500"),
            distance_nm=Decimal("1000"),
            conso_total_t=Decimal("5"),
        )
    )
    db.add(LegKPI(leg_id=2, tonnage_kg=Decimal("0"), co2_emitted_kg=Decimal("30000")))
    await db.flush()


def test_vessel_routes_registered():
    from app.routers import dashboard_perf_router

    paths = {r.path for r in dashboard_perf_router.router.routes}
    assert "/dashboard-perf/vessels/{vessel_id}" in paths


@pytest.mark.asyncio
async def test_vessel_page_requires_kpi_c(db):
    checker = require_permission("kpi", "C")

    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=_rh_user(), db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_vessel_page_unknown_vessel_404(db):
    from app.routers.dashboard_perf_router import dashboard_perf_vessel

    with pytest.raises(HTTPException) as exc:
        await dashboard_perf_vessel(
            999, FakeRequest(), year=2026, method="A", db=db, user=_admin_user()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_vessel_page_renders_200(db):
    from app.routers.dashboard_perf_router import dashboard_perf_vessel

    await _seed_two_legs_mixed_source(db)
    resp = await dashboard_perf_vessel(
        1, FakeRequest(), year=2026, method="A", db=db, user=_admin_user()
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_perf/vessel.html"


@pytest.mark.asyncio
async def test_vessel_htmx_returns_fragment(db):
    from app.routers.dashboard_perf_router import dashboard_perf_vessel

    await _seed_two_legs_mixed_source(db)
    req = FakeRequest()
    req.headers["hx-request"] = "true"
    resp = await dashboard_perf_vessel(1, req, year=2026, method="A", db=db, user=_admin_user())
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_perf/_vessel_fragment.html"


@pytest.mark.asyncio
async def test_vessel_strict_totals_exclude_legacy_but_list_keeps_both(db):
    """NC-04 : les totaux n'agrègent que le voyage event-sourcé, mais les
    2 voyages restent listés — chacun avec son ``source`` explicite."""
    from app.routers.dashboard_perf_router import dashboard_perf_vessel

    await _seed_two_legs_mixed_source(db)
    resp = await dashboard_perf_vessel(
        1, FakeRequest(), year=2026, method="A", db=db, user=_admin_user()
    )
    op = resp.context["op"]

    assert len(op.voyages) == 2
    assert {r.source for r in op.voyages} == {"events", "legacy_kpi"}
    assert op.leg_count == 1
    assert op.co2_total_t == Decimal("50.00")
    assert op.conso_total_t == Decimal("5.00")
    assert op.excluded_non_event_count == 1


# ═══════════════════════════════════════ Page 3 — détail voyage (mrv:C) ═══════════════════════════════════════


def _commercial_user():
    """A ``kpi:C`` mais PAS ``mrv:C`` — voit la page 2, pas le détail voyage."""
    return SimpleNamespace(id=4, full_name="Commercial", username="com1", role="commercial")


async def _seed_legacy_noon_leg(db):
    """1 leg avec seulement 2 NoonReport (aucun NavEvent) — emission_ledger
    bascule sur ``source="legacy_noon"`` (cf. tests/unit/test_emission_ledger.py)."""
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
    db.add(
        NoonReport(
            leg_id=1, recorded_at=now, latitude=0, longitude=0, total_consumption_t=Decimal("1.3")
        )
    )
    db.add(
        NoonReport(
            leg_id=1,
            recorded_at=now,
            latitude=0,
            longitude=0,
            total_consumption_t=Decimal("0.7"),
        )
    )
    await db.flush()


async def _load_1egb5(db):
    """Voyage golden event-sourcé réaliste (mouillage + soutage) — fixtures lot 13."""
    from tests.fixtures.mrv_2025.loader import load_voyage

    return await load_voyage(db, "1EGB5")


def test_voyage_routes_registered():
    from app.routers import dashboard_perf_router

    paths = {r.path for r in dashboard_perf_router.router.routes}
    assert "/dashboard-perf/voyages/{leg_id}" in paths


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
async def test_voyage_detail_unknown_404(db):
    from app.routers.dashboard_perf_router import dashboard_perf_voyage

    with pytest.raises(HTTPException) as exc:
        await dashboard_perf_voyage(999999, FakeRequest(), db=db, user=_admin_user())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_voyage_detail_renders_event_sourced(db):
    """Voyage golden event-sourcé : is_event_sourced=True, géométrie ROB prête."""
    from app.routers.dashboard_perf_router import dashboard_perf_voyage

    fixture = await _load_1egb5(db)
    resp = await dashboard_perf_voyage(fixture.leg.id, FakeRequest(), db=db, user=_admin_user())
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_perf/voyage.html"
    assert resp.context["d"].leg_code == "1EGB5"
    assert resp.context["d"].source == "events"
    assert resp.context["is_event_sourced"] is True
    assert resp.context["rob"]["has_data"] is True


@pytest.mark.asyncio
async def test_voyage_detail_legacy_noon_flags_not_event_sourced(db):
    """NC-04 : un voyage sans capture événementielle (repli legacy_noon) est
    explicitement signalé — jamais présenté comme une donnée event-driven normale."""
    from app.routers.dashboard_perf_router import dashboard_perf_voyage

    await _seed_legacy_noon_leg(db)
    resp = await dashboard_perf_voyage(1, FakeRequest(), db=db, user=_admin_user())
    assert resp.status_code == 200
    assert resp.context["d"].source == "legacy_noon"
    assert resp.context["is_event_sourced"] is False


# ═══════════════════════════════════════ Page 4 — qualité des données (mrv:C) ═══════════════════════════════════════


def _req_csrf():
    """FakeRequest doté d'un jeton CSRF (formulaire confirm-reset, page 4)."""
    req = FakeRequest()
    req.state.csrf_token = "test-csrf"
    return req


def test_quality_routes_registered():
    from app.routers import dashboard_perf_router

    paths = {r.path for r in dashboard_perf_router.router.routes}
    assert "/dashboard-perf/quality" in paths


@pytest.mark.asyncio
async def test_quality_page_requires_mrv_c(db):
    checker = require_permission("mrv", "C")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=_rh_user(), db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_quality_page_renders_empty_db_no_error(db):
    """quality_overview ne lit que des tables event-sourcées : aucune donnée
    en base ne doit jamais faire planter la page (fail-safe, pas de NC-04
    à traiter ici — déjà exclusivement event-driven par construction)."""
    from app.routers.dashboard_perf_router import dashboard_perf_quality

    resp = await dashboard_perf_quality(_req_csrf(), vessel_id=None, db=db, user=_admin_user())
    assert resp.status_code == 200
    assert resp.template.name == "staff/dashboard_perf/quality.html"
    assert resp.context["o"].by_rule == []
    assert resp.context["o"].pending_resets == []


@pytest.mark.asyncio
async def test_quality_page_renders_with_golden_voyage(db):
    from app.routers.dashboard_perf_router import dashboard_perf_quality

    await _load_1egb5(db)
    resp = await dashboard_perf_quality(_req_csrf(), vessel_id=None, db=db, user=_admin_user())
    assert resp.status_code == 200
    assert "o" in resp.context
