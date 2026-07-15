"""Bascule capture événementielle & décommissionnement du legacy MRV.

Couvre :
- le helper ``capture_v2_enabled`` (défaut ON, coupure globale, opt-out par
  navire code/id, fail-open) ;
- la garde du formulaire noon legacy : GET redirige (v2 ON) / renvoie à la page
  navigation (v2 OFF) ; POST refusé 409 (v2 ON) ; rejeu offline 409 explicite ;
- le décommissionnement des routes/symbols legacy MRV (404/405). L'ancienne
  archive lecture seule (``/mrv/archive/events``) a elle-même été supprimée
  (table ``mrv_events`` DROP) — plus rien à couvrir de ce côté.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.feature_flag import FeatureFlag
from app.models.noon_report import NoonReport
from app.services import feature_flags as ff
from tests.integration.conftest import FakeRequest, _setup_leg, disable_capture_v2


def _vessel(code="ANE", vid=1):
    return SimpleNamespace(id=vid, code=code)


# ═════════════════════════ helper capture_v2_enabled ════════════════════════


@pytest.mark.asyncio
async def test_capture_v2_default_on_when_flag_absent(db):
    """Flag absent → défaut ON global (capture v2 imposée)."""
    assert await ff.capture_v2_enabled(db, _vessel(), use_cache=False) is True


@pytest.mark.asyncio
async def test_capture_v2_off_when_globally_disabled(db):
    db.add(FeatureFlag(key="mrv_v2_capture", enabled=False, audience={}))
    await db.flush()
    assert await ff.capture_v2_enabled(db, _vessel(), use_cache=False) is False


@pytest.mark.asyncio
async def test_capture_v2_per_vessel_optout_by_code(db):
    """Opt-out par code navire (double-run inversé) : ce navire OFF, les autres ON."""
    db.add(FeatureFlag(key="mrv_v2_capture", enabled=True, audience={"vessels_off": ["ANE"]}))
    await db.flush()
    assert await ff.capture_v2_enabled(db, _vessel("ANE", 1), use_cache=False) is False
    assert await ff.capture_v2_enabled(db, _vessel("ART", 2), use_cache=False) is True


@pytest.mark.asyncio
async def test_capture_v2_per_vessel_optout_by_id(db):
    db.add(FeatureFlag(key="mrv_v2_capture", enabled=True, audience={"vessels_off": [2]}))
    await db.flush()
    assert await ff.capture_v2_enabled(db, _vessel("ART", 2), use_cache=False) is False
    assert await ff.capture_v2_enabled(db, _vessel("ANE", 1), use_cache=False) is True


@pytest.mark.asyncio
async def test_capture_v2_fail_open_on_db_error():
    """Erreur DB (db=None) → fail-open vers ON : jamais rouvrir le legacy en douce."""
    assert await ff.capture_v2_enabled(None, _vessel(), use_cache=False) is True


# ═══════════════════════ garde du formulaire noon legacy ════════════════════


@pytest.mark.asyncio
async def test_get_noon_form_redirects_to_events_when_v2_on(db, staff_user):
    from app.routers.onboard_router import get_noon_report_form

    leg = await _setup_leg(db)  # navire ANE — capture v2 ON par défaut
    resp = await get_noon_report_form(
        FakeRequest(), vessel_id=None, leg_id=leg.id, db=db, user=staff_user
    )
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith("/onboard/events/new/noon")
    assert "notice=capture_v2" in loc


@pytest.mark.asyncio
async def test_get_noon_form_redirects_to_navigation_when_v2_off(db, staff_user):
    from app.routers.onboard_router import get_noon_report_form

    leg = await _setup_leg(db)
    await disable_capture_v2(db, "ANE")
    resp = await get_noon_report_form(
        FakeRequest(), vessel_id=None, leg_id=leg.id, db=db, user=staff_user
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/onboard/navigation")


@pytest.mark.asyncio
async def test_post_noon_refused_409_when_v2_on(db, staff_user):
    from app.routers.onboard_router import post_noon_report

    leg = await _setup_leg(db)  # capture v2 ON
    form = {"leg_id": str(leg.id), "latitude": "48.5", "longitude": "-5.1"}
    with pytest.raises(HTTPException) as ei:
        await post_noon_report(FakeRequest(form), db=db, user=staff_user)
    assert ei.value.status_code == 409
    assert "événements" in ei.value.detail  # message explicite
    # Aucun noon report écrit.
    cnt = (await db.execute(select(func.count()).select_from(NoonReport))).scalar_one()
    assert cnt == 0


@pytest.mark.asyncio
async def test_offline_replay_refused_409_when_v2_on(db, staff_user):
    """Rejeu de la file offline sur navire basculé → 409 explicite (pas un faux 303
    de dédoublonnage) : jamais de perte silencieuse."""
    from app.routers.onboard_router import post_noon_report

    leg = await _setup_leg(db)  # v2 ON
    form = {
        "leg_id": str(leg.id),
        "latitude": "48.5",
        "longitude": "-5.1",
        "client_uuid": "11111111-2222-3333-4444-555555555555",
    }
    with pytest.raises(HTTPException) as ei:
        await post_noon_report(FakeRequest(form), db=db, user=staff_user)
    assert ei.value.status_code == 409
    assert ei.value.detail


@pytest.mark.asyncio
async def test_post_noon_ok_when_v2_off(db, staff_user):
    """Navire en opt-out (double-run) → ancien flux noon intact (303 + écriture)."""
    from app.routers.onboard_router import post_noon_report

    leg = await _setup_leg(db)
    await disable_capture_v2(db, "ANE")
    form = {"leg_id": str(leg.id), "latitude": "48.5", "longitude": "-5.1", "client_uuid": "ok-1"}
    resp = await post_noon_report(FakeRequest(form), db=db, user=staff_user)
    assert resp.status_code == 303
    cnt = (
        await db.execute(
            select(func.count()).select_from(NoonReport).where(NoonReport.leg_id == leg.id)
        )
    ).scalar_one()
    assert cnt == 1


# ═══════════════════════ décommissionnement legacy MRV ══════════════════════


def _methods(router):
    return {(m, r.path) for r in router.routes for m in (getattr(r, "methods", None) or set())}


def test_legacy_mrv_write_routes_removed():
    """CRUD ``mrv_events`` + exports legacy + ``/params`` retirés (→ 404/405 HTTP)."""
    from app.routers.mrv_router import router

    m = _methods(router)
    for mp in (
        ("POST", "/mrv/legs/{leg_id}/events"),
        ("POST", "/mrv/events/{event_id}/edit"),
        ("POST", "/mrv/events/{event_id}/delete"),
        ("GET", "/mrv/export/dnv.csv"),
        ("GET", "/mrv/export/carbon-report.pdf"),
        ("GET", "/mrv/params"),
        ("POST", "/mrv/params"),
    ):
        assert mp not in m, mp


def test_legacy_crud_symbols_removed():
    import app.routers.mrv_router as mrv

    for name in (
        "add_event",
        "edit_event",
        "delete_event",
        "export_dnv_csv",
        "export_carbon_report_pdf",
        "mrv_params_form",
        "mrv_params_save",
        "mrv_leg_detail",
        "mrv_carbon_report",
        "_apply_event_form",
        "_AdapterMRV",
        "mrv_archive_events",
    ):
        assert not hasattr(mrv, name), name


def test_archive_route_removed():
    """La table ``mrv_events`` a été supprimée (DROP) : plus d'archive à servir."""
    from app.routers.mrv_router import router

    paths = {r.path for r in router.routes}
    assert "/mrv/archive/events" not in paths
