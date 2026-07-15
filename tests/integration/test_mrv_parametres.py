"""MRV LOT 2 — écran /mrv/parametres : GET (C), mutations (S), acceptation.

Vérifie : rendu de l'écran, seed idempotent, gate de permission S (403 pour
un rôle sans S), le critère d'acceptation (seuil 750→800 change le verdict
d'une règle sans redéploiement), l'override par navire, et la traçabilité
(activity_log écrit).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.validation import (
    DashboardParameter,
    ValidationRule,
    ValidationRuleThreshold,
)
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.validation_engine import invalidate_cache, run_rules, seed_reference_data
from tests.integration.conftest import FakeRequest


def _op_user():
    return SimpleNamespace(id=2, full_name="Opérateur", username="op", role="operation")


# ─────────────────────────── routes enregistrées ───────────────────────────


def test_parametres_routes_registered():
    from app.routers import mrv_router

    paths = {r.path for r in mrv_router.router.routes}
    assert "/mrv/parametres" in paths
    assert "/mrv/parametres/init" in paths
    assert "/mrv/parametres/thresholds/{threshold_id}/update" in paths


# ─────────────────────────────── GET (C) ───────────────────────────────


@pytest.mark.asyncio
async def test_get_renders_after_seed(db, staff_user):
    from app.routers.mrv_router import mrv_parametres

    await seed_reference_data(db)
    invalidate_cache()
    resp = await mrv_parametres(FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.template.name == "staff/mrv/parametres.html"
    ctx = resp.context
    assert len(ctx["rules"]) == 33
    # 20 (lot 2) + 1 (lot 6 : R24:fenetre_rattachement_bunker_j) + 1 (lot 4 :
    # R19:delai_alerte_siege_brouillon_h) + 5 (lot 8 : R04:tolerance_datetime_futur_h,
    # R10:delai_confirmation_reset_j, IR03:ir03_min_reports_figes,
    # IR03:ir03_conso_min_t, IR05:ir05_min_reports_figes) + 2 (G1 :
    # R27:tolerance_cutoff_h, R27:rappel_cutoff_avant_j) + 1 (G4 :
    # R28:tolerance_distance_haversine_nm).
    assert len(ctx["thr_global"]) == 30
    assert len(ctx["dash_global"]) == 4
    # 14 (lot 2) + 1 (lot 6) + 1 (lot 4) + 5 (lot 8) + 2 (G1) + 1 (G4) —
    # tous provisoires (Q8).
    assert ctx["provisional_count"] == 24


# ──────────────────────────── init idempotent ────────────────────────────


@pytest.mark.asyncio
async def test_init_route_seeds_idempotently(db, staff_user):
    from app.routers.mrv_router import mrv_parametres_init

    r1 = await mrv_parametres_init(FakeRequest(), db=db, user=staff_user)
    assert r1.status_code == 303
    n1 = len((await db.execute(select(ValidationRule))).scalars().all())
    assert n1 == 33
    # Deuxième appel = no-op (pas de doublon).
    await mrv_parametres_init(FakeRequest(), db=db, user=staff_user)
    n2 = len((await db.execute(select(ValidationRule))).scalars().all())
    assert n2 == 33


# ───────────────────────── gate de permission S ─────────────────────────


@pytest.mark.asyncio
async def test_post_requires_s_permission(db):
    """Le décorateur require_permission('mrv','S') refuse un rôle sans S (403)
    et laisse passer l'administrateur."""
    checker = require_permission("mrv", "S")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=_op_user(), db=db)
    assert exc.value.status_code == 403

    admin = SimpleNamespace(id=1, full_name="Admin", username="admin", role="administrateur")
    assert await checker(FakeRequest(), user=admin, db=db) is admin


# ──────────────────── critère d'acceptation : 750 → 800 ────────────────────


@pytest.mark.asyncio
async def test_threshold_update_flips_rule_verdict(db, staff_user):
    """Modifier le seuil en base change le verdict d'une règle sans redéploiement."""
    from app.routers.mrv_router import mrv_parametres_threshold_update

    await seed_reference_data(db)
    invalidate_cache()
    subj = [SimpleNamespace(conso_l_j=Decimal("780"))]

    before = await run_rules(db, "event", subj, run_id="before")
    r11_before = next(r for r in before.results if r.rule_id == "R11")
    assert r11_before.result == "fail"  # 780 > 750

    # Récupère la ligne R11:seuil_conso_ref_l_j (globale) et la porte à 800.
    row = (
        await db.execute(
            select(ValidationRuleThreshold).where(
                ValidationRuleThreshold.rule_id == "R11",
                ValidationRuleThreshold.parameter_name == "seuil_conso_ref_l_j",
                ValidationRuleThreshold.vessel_id.is_(None),
            )
        )
    ).scalar_one()
    resp = await mrv_parametres_threshold_update(
        row.id,
        FakeRequest(),
        value="800",
        note="calibrage voyage pilote",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303

    after = await run_rules(db, "event", subj, run_id="after")
    r11_after = next(r for r in after.results if r.rule_id == "R11")
    assert r11_after.result == "pass"  # 780 < 800

    # Traçabilité : un activity_log a été écrit pour la mise à jour du seuil.
    logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "mrv_validation_threshold_update")
            )
        )
        .scalars()
        .all()
    )
    assert logs and logs[0].module == "mrv"


# ──────────────────────────── override navire ────────────────────────────


@pytest.mark.asyncio
async def test_vessel_override_created_and_used(db, staff_user):
    from app.routers.mrv_router import mrv_parametres_threshold_override
    from app.services.validation_engine import get_threshold

    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
    await db.flush()
    await seed_reference_data(db)
    invalidate_cache()

    resp = await mrv_parametres_threshold_override(
        FakeRequest(),
        rule_id="R11",
        parameter_name="seuil_conso_ref_l_j",
        vessel_id=1,
        value="900",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    invalidate_cache()

    tv_vessel = await get_threshold(db, "R11", "seuil_conso_ref_l_j", vessel_id=1)
    assert tv_vessel.value == Decimal("900") and tv_vessel.source == "vessel"
    tv_global = await get_threshold(db, "R11", "seuil_conso_ref_l_j")
    assert tv_global.value == Decimal("750") and tv_global.source == "global"


# ─────────────────────────── toggle & dashboard ───────────────────────────


@pytest.mark.asyncio
async def test_rule_toggle(db, staff_user):
    from app.routers.mrv_router import mrv_parametres_rule_toggle

    await seed_reference_data(db)
    r = await db.get(ValidationRule, "R01")
    assert r.active is True
    await mrv_parametres_rule_toggle("R01", FakeRequest(), db=db, user=staff_user)
    r2 = await db.get(ValidationRule, "R01")
    assert r2.active is False


@pytest.mark.asyncio
async def test_dashboard_parameter_update(db, staff_user):
    from app.routers.mrv_router import mrv_parametres_dashboard_update

    await seed_reference_data(db)
    param = (
        await db.execute(
            select(DashboardParameter).where(
                DashboardParameter.parameter_name == "occupancy_rate_pct"
            )
        )
    ).scalar_one()
    await mrv_parametres_dashboard_update(
        param.id, FakeRequest(), value="65", db=db, user=staff_user
    )
    refreshed = await db.get(DashboardParameter, param.id)
    assert refreshed.value == Decimal("65")


@pytest.mark.asyncio
async def test_threshold_update_rejects_invalid_value(db, staff_user):
    from app.routers.mrv_router import mrv_parametres_threshold_update

    await seed_reference_data(db)
    row = (
        await db.execute(
            select(ValidationRuleThreshold)
            .where(
                ValidationRuleThreshold.parameter_name == "seuil_conso_ref_l_j",
                ValidationRuleThreshold.vessel_id.is_(None),
            )
            .limit(1)
        )
    ).scalar_one()
    with pytest.raises(HTTPException) as exc:
        await mrv_parametres_threshold_update(
            row.id, FakeRequest(), value="pas_un_nombre", note="", db=db, user=staff_user
        )
    assert exc.value.status_code == 400
