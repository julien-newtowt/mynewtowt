"""Tests d'intégration — écran datasets OVDLA/OVDBR (LOT 10).

Appelle directement les coroutines de route (patron ``FakeRequest`` + user
factice) sur SQLite en mémoire (conftest d'intégration) :

- rendu de l'écran (mrv:C) ;
- portes de permission : générer exige mrv:M (mrv:C seul → 403) ;
- génération → 303 + gel des entrées + ``activity_log`` ;
- téléchargements OVDLA/OVDBR .xlsx/.csv (content-type).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.bunker import BunkerOperation
from app.models.leg import Leg
from app.models.mrv_dataset import MrvBunkeringEntry, MrvLogAbstractEntry
from app.models.nav_event import ArrivalEvent, DepartureEvent, NavEventEngineReading, NoonEvent
from app.models.port import Port
from app.models.vessel import Vessel
from app.routers import mrv_router as mr
from app.services.referential_env import ensure_vessel_env_defaults, get_vessel_engines
from app.services.validation_engine import invalidate_cache, seed_reference_data
from tests.integration.conftest import FakeRequest

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _setup(db):
    invalidate_cache()
    await seed_reference_data(db)
    invalidate_cache()
    vessel = Vessel(code="ANE", name="Anemos", imo_number="9982938")
    db.add(vessel)
    await db.flush()
    await ensure_vessel_env_defaults(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    p1 = Port(name="Fecamp", country="FR", locode="FRFEC", latitude=49.7, longitude=0.37)
    p2 = Port(name="Belem", country="BR", locode="BRBEL", latitude=-1.45, longitude=-48.5)
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code="1AFRBR6", vessel_id=vessel.id, departure_port_id=p1.id, arrival_port_id=p2.id,
        etd_ref=T0, eta_ref=T0 + timedelta(days=3), etd=T0, eta=T0 + timedelta(days=3),
    )
    db.add(leg)
    await db.flush()

    def _rd(role, fuel):
        return NavEventEngineReading(engine_id=engines[role].id, fuel_counter_l=Decimal(str(fuel)))

    dep = DepartureEvent(
        leg_id=leg.id, vessel_id=vessel.id, status="valide", datetime_utc=T0,
        lat_decimal=Decimal("47.8167"), lon_decimal=Decimal("-3.9333"),
        rob_t=Decimal("100.000"), vessel_condition="laden", cargo_mrv_t=Decimal("540.000"),
    )
    dep.engine_readings = [_rd("PME", 10000), _rd("SME", 8000), _rd("FWD_GEN", 5000), _rd("AFT_GEN", 4000)]
    noon = NoonEvent(
        leg_id=leg.id, vessel_id=vessel.id, status="valide", datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("45.0"), lon_decimal=Decimal("-10.0"),
    )
    noon.engine_readings = [_rd("PME", 11000), _rd("SME", 8600), _rd("FWD_GEN", 5300), _rd("AFT_GEN", 4200)]
    arr = ArrivalEvent(
        leg_id=leg.id, vessel_id=vessel.id, status="valide", datetime_utc=T0 + timedelta(hours=48),
        lat_decimal=Decimal("40.0"), lon_decimal=Decimal("-20.0"),
        rob_t=Decimal("96.000"), vessel_condition="laden", cargo_mrv_t=Decimal("540.000"),
    )
    arr.engine_readings = [_rd("PME", 12000), _rd("SME", 9200), _rd("FWD_GEN", 5600), _rd("AFT_GEN", 4400)]
    db.add_all([dep, noon, arr])
    bunker = BunkerOperation(
        leg_id=leg.id, vessel_id=vessel.id, bdn_number="433421", port_locode="FRFEC",
        delivery_datetime_utc=T0 - timedelta(days=1), fuel_type="MDO",
        mass_t=Decimal("30.054"), density_15c_t_m3=Decimal("0.845"), status="valide_master",
    )
    db.add(bunker)
    await db.flush()
    return vessel, leg


async def test_datasets_screen_renders(db, staff_user):
    vessel, _ = await _setup(db)
    resp = await mr.mrv_datasets(FakeRequest(), vessel_id=vessel.id, year=None, db=db, user=staff_user)
    assert resp.status_code == 200


async def test_generate_requires_mrv_m(db):
    """L'aperçu est mrv:C ; générer exige mrv:M (armement a mrv:C seulement)."""
    checker_c = mr.require_permission("mrv", "C")
    checker_m = mr.require_permission("mrv", "M")
    admin = SimpleNamespace(id=1, role="administrateur", username="a", full_name="A")
    armement = SimpleNamespace(id=3, role="armement", username="arm", full_name="Arm")
    # admin : C et M OK.
    await checker_c(FakeRequest(), user=admin, db=db)
    await checker_m(FakeRequest(), user=admin, db=db)
    # armement : C OK, M refusé.
    await checker_c(FakeRequest(), user=armement, db=db)
    with pytest.raises(HTTPException) as ei:
        await checker_m(FakeRequest(), user=armement, db=db)
    assert ei.value.status_code == 403


async def test_generate_snapshots_and_audits(db, staff_user):
    vessel, _ = await _setup(db)
    resp = await mr.mrv_datasets_generate(
        FakeRequest(), vessel_id=vessel.id, year=None, db=db, user=staff_user
    )
    assert resp.status_code == 303

    la = (await db.execute(select(MrvLogAbstractEntry))).scalars().all()
    br = (await db.execute(select(MrvBunkeringEntry))).scalars().all()
    assert len(la) == 2  # Departure + Arrival (le Noon ne produit pas d'entrée)
    assert len(br) == 1  # le soutage validé Master
    assert all(e.source_system == "MyTOWT" for e in la)

    logs = (
        await db.execute(select(ActivityLog).where(ActivityLog.action == "mrv_dataset_generate"))
    ).scalars().all()
    assert len(logs) == 1

    # Idempotent : re-générer ne crée pas de doublon.
    await mr.mrv_datasets_generate(FakeRequest(), vessel_id=vessel.id, year=None, db=db, user=staff_user)
    la2 = (await db.execute(select(MrvLogAbstractEntry))).scalars().all()
    assert len(la2) == 2


async def test_downloads_content_types(db, staff_user):
    vessel, _ = await _setup(db)
    xls = await mr.mrv_datasets_ovdla_xlsx(vessel_id=vessel.id, year=None, db=db, user=staff_user)
    assert xls.media_type == _XLSX
    assert "OVDLA_ANE" in xls.headers["content-disposition"]

    csv = await mr.mrv_datasets_ovdla_csv(vessel_id=vessel.id, year=None, db=db, user=staff_user)
    assert csv.media_type == "text/csv"
    body = csv.body.decode()
    assert body.splitlines()[0].split(",")[0] == "IMO"

    br_xls = await mr.mrv_datasets_ovdbr_xlsx(vessel_id=vessel.id, year=None, db=db, user=staff_user)
    assert br_xls.media_type == _XLSX
    br_csv = await mr.mrv_datasets_ovdbr_csv(vessel_id=vessel.id, year=None, db=db, user=staff_user)
    assert "BDN_Number" in br_csv.body.decode()


async def test_year_filter(db, staff_user):
    vessel, _ = await _setup(db)
    # Année 2026 : toutes les lignes tombent dedans.
    resp = await mr.mrv_datasets(FakeRequest(), vessel_id=vessel.id, year=2026, db=db, user=staff_user)
    assert resp.status_code == 200
    # Année 2020 : aucune ligne → écran rendu, aperçu vide.
    resp2 = await mr.mrv_datasets(FakeRequest(), vessel_id=vessel.id, year=2020, db=db, user=staff_user)
    assert resp2.status_code == 200
