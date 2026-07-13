"""Tests d'intégration — rapports générés MRV & workflow (LOT 5).

Appelle directement les coroutines de route (patron ``FakeRequest`` + user
factice) sur une base SQLite en mémoire (FK activées, conftest d'intégration) :

- workflow complet generate → validate-master (captain) → validate-siege
  (mrv:M, Carbon) + écriture ``activity_log`` ;
- un user ``marins`` (captain:C seulement) ne peut PAS valider Master (403) ;
- validate-siege sur un Noon → refus propre (400) ;
- PDF smoke test (3 types → HTTP 200, content-type PDF) ;
- correction de champ : R18 (justification vide → 400) + audit tracé ;
- rendu des écrans voyages (liste + détail).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.env_report import EnvReport
from app.models.leg import Leg
from app.models.nav_event import (
    ArrivalEvent,
    DepartureEvent,
    NavEventEngineReading,
    NoonEvent,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.routers import mrv_router as mr
from app.services import referential_env
from app.services.referential_env import ensure_vessel_env_defaults, get_vessel_engines
from app.services.validation_engine import invalidate_cache, seed_reference_data
from tests.integration.conftest import FakeRequest

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def _setup(db):
    invalidate_cache()
    referential_env.invalidate_emission_factor_cache()
    await seed_reference_data(db)
    invalidate_cache()

    vessel = Vessel(code="ANE", name="Anemos", imo_number="9876543")
    db.add(vessel)
    await db.flush()
    await ensure_vessel_env_defaults(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    p1 = Port(name="Fécamp", country="FR", locode="FRFEC", latitude=49.7, longitude=0.37)
    p2 = Port(name="Belém", country="BR", locode="BRBEL", latitude=-1.45, longitude=-48.5)
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code="1AFRBR6",
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd_ref=T0,
        eta_ref=T0 + timedelta(days=5),
        etd=T0,
        eta=T0 + timedelta(days=5),
    )
    leg2 = Leg(
        leg_code="1BBRFR6",
        vessel_id=vessel.id,
        departure_port_id=p2.id,
        arrival_port_id=p1.id,
        etd_ref=T0 + timedelta(days=3),
        eta_ref=T0 + timedelta(days=8),
        etd=T0 + timedelta(days=3),
        eta=T0 + timedelta(days=8),
    )
    db.add_all([leg, leg2])
    await db.flush()

    def _rd(role, fuel):
        return NavEventEngineReading(engine_id=engines[role].id, fuel_counter_l=Decimal(str(fuel)))

    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        lat_decimal=Decimal("50.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("100.000"),
        vessel_condition="laden",
        cargo_bl_t=Decimal("900.000"),
        cargo_mrv_t=Decimal("950.000"),
    )
    dep.engine_readings = [
        _rd("PME", 10000),
        _rd("SME", 8000),
        _rd("FWD_GEN", 5000),
        _rd("AFT_GEN", 4000),
    ]
    noon = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("47.0"),
        lon_decimal=Decimal("-5.0"),
    )
    noon.engine_readings = [
        _rd("PME", 11000),
        _rd("SME", 8600),
        _rd("FWD_GEN", 5300),
        _rd("AFT_GEN", 4200),
    ]
    arr = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=48),
        lat_decimal=Decimal("44.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("96.000"),
        vessel_condition="laden",
    )
    arr.engine_readings = [
        _rd("PME", 12000),
        _rd("SME", 9200),
        _rd("FWD_GEN", 5600),
        _rd("AFT_GEN", 4400),
    ]
    # Départ du voyage suivant (pour le rapport d'escale).
    dep2 = DepartureEvent(
        leg_id=leg2.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=72),
        lat_decimal=Decimal("44.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("95.500"),
        vessel_condition="laden",
    )
    dep2.engine_readings = [_rd("PME", 12300)]
    db.add_all([dep, noon, arr, dep2])
    await db.flush()
    return SimpleNamespace(
        vessel=vessel, leg=leg, leg2=leg2, dep=dep, noon=noon, arr=arr, dep2=dep2
    )


async def _get_report(db, leg_id, report_type):
    return (
        (
            await db.execute(
                select(EnvReport).where(
                    EnvReport.leg_id == leg_id, EnvReport.report_type == report_type
                )
            )
        )
        .scalars()
        .first()
    )


# ════════════════════════════════════════════════════════════ Workflow


@pytest.mark.asyncio
async def test_full_workflow_generate_master_siege(db, staff_user):
    s = await _setup(db)

    resp = await mr.mrv_generate_report(s.leg.id, "carbon", FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    report = await _get_report(db, s.leg.id, "carbon")
    assert report is not None and report.status == "brouillon"

    # Master (staff_user = administrateur ⇒ captain:CMS).
    resp = await mr.mrv_report_validate_master(report.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert report.status == "valide_master"

    # Siège (mrv:M) — Carbon uniquement.
    resp = await mr.mrv_report_validate_siege(report.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert report.status == "valide_siege"

    # Audit trail : les 3 actions sont tracées.
    actions = (await db.execute(select(ActivityLog.action))).scalars().all()
    assert "mrv_report_generate" in actions
    assert "mrv_report_validate_master" in actions
    assert "mrv_report_validate_siege" in actions


@pytest.mark.asyncio
async def test_marins_cannot_validate_master_403(db):
    await _setup(db)
    checker = mr.require_permission("captain", "M")
    marins = SimpleNamespace(id=2, role="marins", full_name="Marin", username="marin")
    with pytest.raises(HTTPException) as ei:
        await checker(FakeRequest(), user=marins, db=db)
    assert ei.value.status_code == 403
    # Cohérence de la matrice : marins a captain:C mais pas :M.
    from app.permissions import has_permission_effective

    assert await has_permission_effective(db, "marins", "captain", "M") is False
    assert await has_permission_effective(db, "manager_maritime", "captain", "M") is True


@pytest.mark.asyncio
async def test_validate_siege_on_noon_refused(db, staff_user):
    s = await _setup(db)
    await mr.mrv_generate_report(
        s.leg.id, "noon", FakeRequest(form={"event_id": str(s.noon.id)}), db=db, user=staff_user
    )
    noon_report = await _get_report(db, s.leg.id, "noon")
    await mr.mrv_report_validate_master(noon_report.id, FakeRequest(), db=db, user=staff_user)

    with pytest.raises(HTTPException) as ei:
        await mr.mrv_report_validate_siege(noon_report.id, FakeRequest(), db=db, user=staff_user)
    assert ei.value.status_code == 400


# ════════════════════════════════════════════════════════════ PDF smoke


@pytest.mark.asyncio
async def test_pdf_smoke_three_types(db, staff_user):
    s = await _setup(db)
    # Génère les 3 types.
    await mr.mrv_generate_report(s.leg.id, "carbon", FakeRequest(), db=db, user=staff_user)
    await mr.mrv_generate_report(
        s.leg.id, "noon", FakeRequest(form={"event_id": str(s.noon.id)}), db=db, user=staff_user
    )
    await mr.mrv_generate_report(
        s.leg.id,
        "stopover",
        FakeRequest(form={"arrival_event_id": str(s.arr.id), "departure_event_id": str(s.dep2.id)}),
        db=db,
        user=staff_user,
    )

    for rtype in ("carbon", "noon", "stopover"):
        report = await _get_report(db, s.leg.id, rtype)
        assert report is not None, rtype
        resp = await mr.mrv_report_pdf(report.id, db=db, user=staff_user)
        assert resp.status_code == 200, rtype
        assert resp.media_type == "application/pdf", rtype
        assert bytes(resp.body).startswith(b"%PDF"), rtype


# ════════════════════════════════════════════════════════════ Field modify


@pytest.mark.asyncio
async def test_field_modify_requires_justification_and_records(db, staff_user):
    s = await _setup(db)
    await mr.mrv_generate_report(s.leg.id, "carbon", FakeRequest(), db=db, user=staff_user)
    report = await _get_report(db, s.leg.id, "carbon")
    await mr.mrv_report_validate_master(report.id, FakeRequest(), db=db, user=staff_user)

    # R18 — justification vide → 400.
    with pytest.raises(HTTPException) as ei:
        await mr.mrv_report_field_modify(
            report.id,
            FakeRequest(
                form={
                    "field_name": "cargo_bl_t",
                    "corrected_value": "910",
                    "justification": "  ",
                    "resulting_quality_status": "corrected",
                }
            ),
            db=db,
            user=staff_user,
        )
    assert ei.value.status_code == 400

    # Correction valide → 303 + audit + snapshot mis à jour.
    resp = await mr.mrv_report_field_modify(
        report.id,
        FakeRequest(
            form={
                "field_name": "cargo_bl_t",
                "corrected_value": "910",
                "justification": "Correction B/L",
                "resulting_quality_status": "corrected",
            }
        ),
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    assert report.payload["cargo_bl_t"] == "910"
    actions = (await db.execute(select(ActivityLog.action))).scalars().all()
    assert "mrv_report_field_modify" in actions


# ════════════════════════════════════════════════════════════ Écrans


@pytest.mark.asyncio
async def test_voyages_screens_render(db, staff_user):
    s = await _setup(db)
    await mr.mrv_generate_report(s.leg.id, "carbon", FakeRequest(), db=db, user=staff_user)

    resp = await mr.mrv_voyages(FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.template.name == "staff/mrv/voyages.html"

    detail = await mr.mrv_voyage_detail(s.leg.id, FakeRequest(), db=db, user=staff_user)
    assert detail.status_code == 200
    assert detail.template.name == "staff/mrv/voyage_detail.html"
    assert len(detail.context["event_rows"]) == 3
    assert len(detail.context["reports"]) == 1
