"""LOT 8 — écran /mrv/qualite, routage des alertes, cron quality-run.

Patron ``tests/integration/test_onboard_events.py`` (coroutines de route
appelées directement, ``db``/``FakeRequest``/``staff_user`` du conftest) :

- **Routage** : R24 → notification ``administrateur`` ; R14 critique →
  ``manager_maritime`` + ``administrateur`` ; idempotence (pas de doublon en
  24 h) ; ré-alerte au-delà de 24 h si non résolu ; **l'acquittement stoppe
  la re-notification** ;
- **Écran** : GET + filtres (règle/sévérité/résultat) + compteurs ;
- **Actions** : confirm-reset (trace + fait passer R10 + nouvelle base de
  calcul), acknowledge (trace + erreurs 404/400/409) ;
- **Cron** ``POST /api/mrv/quality-run`` : 503 sans token / 403 mauvais token /
  200 + compteurs ``{legs_scanned, checks, fails}``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.leg import Leg
from app.models.nav_event import ArrivalEvent, DepartureEvent, NavEventEngineReading
from app.models.notification import Notification
from app.models.port import Port
from app.models.validation import QualityCheckResult
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import bunkering
from app.services import validation_rules_catalog as vrc
from app.services.referential_env import ensure_vessel_env_defaults, get_vessel_engines
from app.services.validation_engine import invalidate_cache, run_rules, seed_reference_data
from tests.integration.conftest import FakeRequest

T0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


async def _seed(db):
    invalidate_cache()
    await seed_reference_data(db)
    invalidate_cache()


async def _vessel(db, code="ANE", with_engines=False):
    v = Vessel(code=code, name="Anemos")
    db.add(v)
    await db.flush()
    if with_engines:
        await ensure_vessel_env_defaults(db, v)
    return v


async def _leg(db, vessel, code="1AFRBR6"):
    p1 = Port(locode="FRFEC", name="Fécamp", country="FR")
    p2 = Port(locode="BRBEL", name="Belém", country="BR")
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code=code,
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd_ref=T0,
        eta_ref=T0 + timedelta(days=5),
        etd=T0,
        eta=T0 + timedelta(days=5),
    )
    db.add(leg)
    await db.flush()
    return leg


async def _bunker(db, vessel, bdn="BDN-24"):
    return await bunkering.create_draft(
        db,
        vessel=vessel,
        author_user_id=1,
        bdn_number=bdn,
        port_locode="FRFEC",
        delivery_datetime_utc=T0,
        mass_t=Decimal("20"),
        density_15c_t_m3=Decimal("0.845"),
    )


async def _notifs(db, role):
    return list(
        (await db.execute(select(Notification).where(Notification.target_role == role)))
        .scalars()
        .all()
    )


# ═══════════════════════════════════════════════════ routes & permissions


def test_qualite_routes_registered():
    from app.routers import mrv_router

    paths = {r.path for r in mrv_router.router.routes}
    assert "/mrv/qualite" in paths
    assert "/mrv/qualite/engine-readings/{reading_id}/confirm-reset" in paths
    assert "/mrv/qualite/{qcr_id}/acknowledge" in paths
    api_paths = {r.path for r in mrv_router.api_router.routes}
    assert "/api/mrv/quality-run" in api_paths


@pytest.mark.asyncio
async def test_qualite_requires_mrv_c_and_actions_mrv_m(db):
    viewer = SimpleNamespace(id=97, full_name="Data", username="da", role="data_analyst")
    rh_user = SimpleNamespace(id=98, full_name="RH", username="rh1", role="rh")
    checker_c = require_permission("mrv", "C")
    with pytest.raises(HTTPException) as exc:
        await checker_c(FakeRequest(), user=rh_user, db=db)
    assert exc.value.status_code == 403
    assert await checker_c(FakeRequest(), user=viewer, db=db) is viewer

    checker_m = require_permission("mrv", "M")
    armement = SimpleNamespace(id=96, full_name="Arm", username="arm", role="armement")
    with pytest.raises(HTTPException) as exc:
        await checker_m(FakeRequest(), user=armement, db=db)
    assert exc.value.status_code == 403


# ═══════════════════════════════════════════════════ routage R24 (admin) + dédup


@pytest.mark.asyncio
async def test_r24_alert_routed_to_admin_once_then_deduped(db, staff_user):
    """La validation Master d'un soutage non recoupé FLGO alerte
    l'``administrateur`` UNE fois ; un re-run immédiat ne double pas (dédup
    24 h + notification active)."""
    await _seed(db)
    vessel = await _vessel(db)
    bunker = await _bunker(db, vessel)

    await bunkering.validate_master(db, bunker, SimpleNamespace(id=1))
    fails = (
        (
            await db.execute(
                select(QualityCheckResult).where(
                    QualityCheckResult.rule_id == "R24", QualityCheckResult.result == "fail"
                )
            )
        )
        .scalars()
        .all()
    )
    assert fails, "R24 doit échouer (aucune lecture FLGO Received)"
    admin_notifs = await _notifs(db, "administrateur")
    assert len(admin_notifs) == 1
    assert "R24" in admin_notifs[0].title

    # Re-run immédiat (même sujet/règle) → aucune nouvelle notification.
    await vrc.run_bunker_rules_and_route(db, bunker)
    assert len(await _notifs(db, "administrateur")) == 1


@pytest.mark.asyncio
async def test_r24_realerts_after_24h_unless_acknowledged(db, staff_user):
    """Au-delà de 24 h non résolu (et notification archivée) → ré-alerte ;
    après ACQUITTEMENT → plus jamais de re-notification."""
    from app.routers.mrv_router import mrv_qualite_acknowledge

    await _seed(db)
    vessel = await _vessel(db)
    bunker = await _bunker(db, vessel)
    await bunkering.validate_master(db, bunker, SimpleNamespace(id=1))
    assert len(await _notifs(db, "administrateur")) == 1

    async def _age_all_and_archive():
        old = datetime.now(UTC) - timedelta(hours=25)
        rows = (
            (
                await db.execute(
                    select(QualityCheckResult).where(QualityCheckResult.rule_id == "R24")
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            r.executed_at = old
        for n in await _notifs(db, "administrateur"):
            n.is_archived = True
        await db.flush()
        return rows

    # 1) fail vieux de 25 h, non acquitté, notification archivée → RÉ-ALERTE.
    await _age_all_and_archive()
    await vrc.run_bunker_rules_and_route(db, bunker)
    assert len(await _notifs(db, "administrateur")) == 2

    # 2) même situation mais fail ACQUITTÉ → la re-notification s'arrête.
    rows = await _age_all_and_archive()
    latest = max(rows, key=lambda r: r.id)
    resp = await mrv_qualite_acknowledge(latest.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    await vrc.run_bunker_rules_and_route(db, bunker)
    assert len(await _notifs(db, "administrateur")) == 2  # inchangé


# ═══════════════════════════════════════════════════ routage R14 critique


@pytest.mark.asyncio
async def test_r14_critical_routes_manager_and_admin(db, staff_user):
    """R14 critique (bloquant) → ``manager_maritime`` ET ``administrateur``."""
    await _seed(db)
    vessel = await _vessel(db, with_engines=True)
    leg = await _leg(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        rob_t=Decimal("100"),
        vessel_condition="laden",
    )
    dep.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("10000"))
    ]
    arr = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        rob_t=Decimal("90"),
        vessel_condition="laden",
    )  # écart ≈ 9,2 t
    arr.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("11000"))
    ]
    db.add_all([dep, arr])
    await db.flush()

    summary = await run_rules(db, "voyage", [leg], vessel=vessel, leg=leg, persist_passes=False)
    fails = [r for r in summary.results if r.result == "fail"]
    assert any(r.rule_id == "R14" and r.severity_applied == "bloquant" for r in fails)
    await vrc.route_alerts(db, fails)
    assert len(await _notifs(db, "manager_maritime")) == 1
    assert len(await _notifs(db, "administrateur")) == 1


# ═══════════════════════════════════════════════════ écran GET + filtres


@pytest.mark.asyncio
async def test_qualite_screen_renders_with_filters_and_counters(db, staff_user):
    from app.routers.mrv_router import mrv_qualite

    await _seed(db)
    vessel = await _vessel(db)
    bunker = await _bunker(db, vessel)
    await bunkering.validate_master(db, bunker, SimpleNamespace(id=1))  # R23+R24 fails

    resp = await mrv_qualite(
        FakeRequest(),
        rule=None,
        severity=None,
        vessel_id=None,
        leg_id=None,
        result=None,
        date_from=None,
        date_to=None,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/mrv/qualite.html"
    all_rules = {row["qcr"].rule_id for row in resp.context["rows"]}
    assert {"R23", "R24"} <= all_rules
    assert resp.context["severity_counts"]["warning"] >= 2
    assert resp.context["can_act"] is True  # administrateur ⇒ mrv:M

    # Filtre règle → uniquement R24.
    resp_r24 = await mrv_qualite(
        FakeRequest(),
        rule="R24",
        severity=None,
        vessel_id=None,
        leg_id=None,
        result="fail",
        date_from=None,
        date_to=None,
        db=db,
        user=staff_user,
    )
    assert {row["qcr"].rule_id for row in resp_r24.context["rows"]} == {"R24"}

    # Filtre sévérité inconnue de la ligne → aucune ligne.
    resp_blk = await mrv_qualite(
        FakeRequest(),
        rule="R24",
        severity="bloquant",
        vessel_id=None,
        leg_id=None,
        result=None,
        date_from=None,
        date_to=None,
        db=db,
        user=staff_user,
    )
    assert resp_blk.context["rows"] == []

    # Filtre période dans le futur → aucune ligne.
    resp_future = await mrv_qualite(
        FakeRequest(),
        rule=None,
        severity=None,
        vessel_id=None,
        leg_id=None,
        result=None,
        date_from="2030-01-01",
        date_to=None,
        db=db,
        user=staff_user,
    )
    assert resp_future.context["rows"] == []


# ═══════════════════════════════════════════════════ confirm-reset (R10)


async def _regressed_chain(db, *, flagged_by_master=True):
    """Departure(10000 L) → Arrival(900 L) : compteur régressant.

    ``flagged_by_master=True`` = le bord a déclaré le reset (IR04 passe) mais
    l'Administrateur ne l'a pas encore confirmé (R10 échoue)."""
    vessel = await _vessel(db, with_engines=True)
    leg = await _leg(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    now = datetime.now(UTC)
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=now - timedelta(hours=25),
        rob_t=Decimal("100"),
        vessel_condition="laden",
    )
    dep.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("10000"))
    ]
    arr = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=now - timedelta(hours=1),
        rob_t=Decimal("99"),
        vessel_condition="laden",
    )
    arr.engine_readings = [
        NavEventEngineReading(
            engine_id=engines["PME"].id,
            fuel_counter_l=Decimal("900"),
            is_counter_reset=flagged_by_master,
        )
    ]
    db.add_all([dep, arr])
    await db.flush()
    return vessel, leg, dep, arr


@pytest.mark.asyncio
async def test_confirm_reset_traces_and_makes_r10_pass(db, staff_user):
    from app.routers.mrv_router import mrv_qualite, mrv_qualite_confirm_reset

    await _seed(db)
    vessel, leg, dep, arr = await _regressed_chain(db)
    reading = arr.engine_readings[0]

    # AVANT : R10 échoue (régression non confirmée, routée admin) ; IR04 passe
    # (reset documenté par le bord).
    s1 = await run_rules(db, "event", [dep, arr], vessel=vessel, leg=leg, persist_passes=False)
    r10_fails = [r for r in s1.results if r.rule_id == "R10" and r.result == "fail"]
    assert r10_fails and r10_fails[0].severity_applied == "warning"
    assert reading.id in (r10_fails[0].details or {}).get("reading_ids", [])
    assert not [r for r in s1.results if r.rule_id == "IR04" and r.result == "fail"]

    # L'écran expose le reset en attente.
    scr = await mrv_qualite(
        FakeRequest(),
        rule=None,
        severity=None,
        vessel_id=None,
        leg_id=None,
        result=None,
        date_from=None,
        date_to=None,
        db=db,
        user=staff_user,
    )
    assert any(rd.id == reading.id for rd in scr.context["pending_resets"])

    # CONFIRMATION (mrv:M) → 303 + champs renseignés + activity trail.
    resp = await mrv_qualite_confirm_reset(reading.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert reading.reset_confirmed_by == staff_user.id
    assert reading.reset_confirmed_at is not None
    assert reading.is_counter_reset is True
    logs = (
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "mrv_counter_reset_confirm")
            )
        )
        .scalars()
        .all()
    )
    assert logs and logs[0].module == "mrv"

    # APRÈS : R10 passe (nouvelle base de référence) et le calcul reprend
    # (conso = valeur aval du compteur, reset appliqué).
    s2 = await run_rules(db, "event", [dep, arr], vessel=vessel, leg=leg, persist_passes=False)
    assert not [r for r in s2.results if r.rule_id == "R10" and r.result == "fail"]
    from app.services import inter_event_compute as iec

    comp = await iec.compute_leg(db, leg)
    assert comp.intervals[0].counter_anomaly is False
    assert comp.intervals[0].engines[reading.engine_id].reset_applied is True

    # Double confirmation → 409.
    with pytest.raises(HTTPException) as exc:
        await mrv_qualite_confirm_reset(reading.id, FakeRequest(), db=db, user=staff_user)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_confirm_reset_unknown_reading_404(db, staff_user):
    await _seed(db)
    from app.routers.mrv_router import mrv_qualite_confirm_reset

    with pytest.raises(HTTPException) as exc:
        await mrv_qualite_confirm_reset(999999, FakeRequest(), db=db, user=staff_user)
    assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════ acknowledge


@pytest.mark.asyncio
async def test_acknowledge_flow_and_errors(db, staff_user):
    from app.routers.mrv_router import mrv_qualite_acknowledge

    await _seed(db)
    vessel = await _vessel(db)
    bunker = await _bunker(db, vessel)
    await bunkering.validate_master(db, bunker, SimpleNamespace(id=1))
    fail = (
        (await db.execute(select(QualityCheckResult).where(QualityCheckResult.rule_id == "R24")))
        .scalars()
        .first()
    )

    resp = await mrv_qualite_acknowledge(fail.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert fail.acknowledged_at is not None
    assert fail.acknowledged_by == staff_user.id
    logs = (
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "mrv_quality_acknowledge")
            )
        )
        .scalars()
        .all()
    )
    assert logs and logs[0].entity_id == fail.id

    # 409 double acquittement.
    with pytest.raises(HTTPException) as exc:
        await mrv_qualite_acknowledge(fail.id, FakeRequest(), db=db, user=staff_user)
    assert exc.value.status_code == 409
    # 404 inconnu.
    with pytest.raises(HTTPException) as exc:
        await mrv_qualite_acknowledge(999999, FakeRequest(), db=db, user=staff_user)
    assert exc.value.status_code == 404
    # 400 sur un pass (seul un échec s'acquitte).
    ok = QualityCheckResult(
        rule_id="R24",
        subject_type="bunker_operations",
        subject_id=bunker.id,
        run_id="x",
        result="pass",
    )
    db.add(ok)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await mrv_qualite_acknowledge(ok.id, FakeRequest(), db=db, user=staff_user)
    assert exc.value.status_code == 400


# ═══════════════════════════════════════════ déclencheurs report & flgo (routes)


@pytest.mark.asyncio
async def test_report_validate_master_triggers_r22(db, staff_user):
    """La route de validation Master d'un rapport exécute le scope ``report`` :
    un Carbon divergent de la Σ des Noon (> tolérance 1 t) est signalé R22 —
    JAMAIS corrigé (le payload du Carbon reste intact)."""
    from app.models.env_report import EnvReport
    from app.routers.mrv_router import mrv_report_validate_master

    await _seed(db)
    vessel = await _vessel(db)
    leg = await _leg(db, vessel)
    carbon = EnvReport(
        leg_id=leg.id,
        report_type="carbon",
        status="brouillon",
        payload={"totals": {"conso_total_t": "10"}},
    )
    noon = EnvReport(
        leg_id=leg.id,
        report_type="noon",
        status="valide_master",
        payload={"interval": {"conso_total_t": "7"}},
    )
    db.add_all([carbon, noon])
    await db.flush()

    resp = await mrv_report_validate_master(carbon.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    r22 = (
        (
            await db.execute(
                select(QualityCheckResult).where(
                    QualityCheckResult.rule_id == "R22", QualityCheckResult.result == "fail"
                )
            )
        )
        .scalars()
        .all()
    )
    assert r22 and r22[0].severity_applied == "warning"
    assert r22[0].details["carbon_corrector"] is False
    assert carbon.payload["totals"]["conso_total_t"] == "10"  # jamais corrigé


@pytest.mark.asyncio
async def test_flgo_import_route_triggers_r25(db):
    """L'import xlsx FLGO exécute le scope ``flgo`` : une lecture au détail
    par compartiment incohérent (Σ 25 vs total 31 > tolérance 2 m³) est
    signalée R25 — la donnée FLGO n'est JAMAIS corrigée."""
    import io

    import openpyxl
    from fastapi import UploadFile

    from app.models.flgo import FlgoReading
    from app.routers.mrv_router import mrv_flgo_import

    await _seed(db)
    vessel = await _vessel(db)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main sheet"
    ws.append(["NewTOWT"])
    ws.append([])
    ws.append(["TestVessel"])
    ws.append(["All - test range"])
    ws.append([None, "Product"])
    ws.append([None, "Category: Fuel"])
    ws.append([None, "Diesel Oil"])
    ws.append(
        [
            None,
            None,
            "",
            "Operation date",
            "14 - GO DB B",
            "15 - GO DB T",
            "Total volume [m3]",
            "ROB [m3]",
            "Remarks",
            "Docs",
        ]
    )
    ws.append(
        [
            None,
            None,
            "Measurement",
            "06/07/2026 22:13",
            "14.6 m3 (12.76 t)",
            "10.4 m3 (9.03 t)",
            "31",
            "31",
            "",
            "0",
        ]
    )
    buf = io.BytesIO()
    wb.save(buf)

    user = SimpleNamespace(id=1, full_name="MRV", username="mrv", role="operation")
    resp = await mrv_flgo_import(
        FakeRequest(),
        vessel_id=vessel.id,
        file=UploadFile(filename="flgo.xlsx", file=io.BytesIO(buf.getvalue())),
        db=db,
        user=user,
    )
    assert resp.status_code == 200
    r25 = (
        (
            await db.execute(
                select(QualityCheckResult).where(
                    QualityCheckResult.rule_id == "R25", QualityCheckResult.result == "fail"
                )
            )
        )
        .scalars()
        .all()
    )
    assert r25 and r25[0].severity_applied == "warning"
    assert r25[0].details["volet"] == "interne"
    # Jamais corrigé : le total déclaré reste 31.
    reading = (await db.execute(select(FlgoReading))).scalar_one()
    assert reading.total_volume_m3 == Decimal("31")


# ═══════════════════════════════════════════════════ cron quality-run


@pytest.mark.asyncio
async def test_quality_run_cron_503_without_token(db, monkeypatch):
    from app.routers import mrv_router
    from app.routers.mrv_router import mrv_quality_run_cron

    monkeypatch.setattr(mrv_router.settings, "mrv_quality_api_token", None)
    with pytest.raises(HTTPException) as exc:
        await mrv_quality_run_cron(FakeRequest(), db=db)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_quality_run_cron_403_bad_token(db, monkeypatch):
    from app.routers import mrv_router
    from app.routers.mrv_router import mrv_quality_run_cron

    monkeypatch.setattr(mrv_router.settings, "mrv_quality_api_token", "s3cret")
    req = FakeRequest()
    req.headers = {"x-api-token": "wrong"}
    with pytest.raises(HTTPException) as exc:
        await mrv_quality_run_cron(req, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_quality_run_cron_ok_scans_active_legs(db, monkeypatch, staff_user):
    """200 + compteurs : le run nocturne balaie le leg ACTIF (événements +
    voyage + inter-rapports) et persiste les fails au journal."""
    from app.routers import mrv_router
    from app.routers.mrv_router import mrv_quality_run_cron

    await _seed(db)
    vessel, leg, _dep, _arr = await _regressed_chain(db, flagged_by_master=False)
    # Un leg clôturé (approuvé) du même navire — NE doit PAS être scanné.
    p3 = Port(locode="VNSGN", name="Saigon", country="VN")
    db.add(p3)
    await db.flush()
    closed = Leg(
        leg_code="1BBRVN6",
        vessel_id=vessel.id,
        departure_port_id=leg.arrival_port_id,
        arrival_port_id=p3.id,
        etd_ref=T0 + timedelta(days=10),
        eta_ref=T0 + timedelta(days=20),
        etd=T0 + timedelta(days=10),
        eta=T0 + timedelta(days=20),
        closure_approved_at=datetime.now(UTC),
    )
    db.add(closed)
    await db.flush()

    monkeypatch.setattr(mrv_router.settings, "mrv_quality_api_token", "s3cret")
    req = FakeRequest()
    req.headers = {"x-api-token": "s3cret"}
    resp = await mrv_quality_run_cron(req, db=db)
    assert resp.status_code == 200
    body = json.loads(bytes(resp.body))
    assert set(body) == {"legs_scanned", "checks", "fails"}
    assert body["legs_scanned"] == 1  # le leg clôturé est exclu
    assert body["checks"] > 0
    assert body["fails"] >= 2  # IR04 (régression non documentée) + R10 au moins
    # Les fails du run nocturne sont au journal (avec sévérités appliquées).
    rows = (
        (await db.execute(select(QualityCheckResult).where(QualityCheckResult.result == "fail")))
        .scalars()
        .all()
    )
    assert any(r.rule_id == "IR04" and r.severity_applied == "bloquant" for r in rows)
    assert any(r.rule_id == "R10" for r in rows)
