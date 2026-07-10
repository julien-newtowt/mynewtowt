"""MRV — moteur de calcul/qualité legacy (mrv_compute) + sync (mrv_sync).

LOT 14 — bascule : le CRUD manuel ``mrv_events`` (add/edit/delete), l'export CSV
DNV et l'écran ``/params`` ont été RETIRÉS des routes ; les tests qui les
exerçaient ont donc été supprimés (couverture « routes retirées » reprise par
``tests/integration/test_mrv_bascule.py`` : 404/405). Restent ici les tests des
modules LEGACY conservés en INERTE (importables mais plus appelés par les
routes) : ``mrv_compute.recompute_leg`` (deltas compteurs, ROB chaîné, qualité)
et ``mrv_sync.ensure_from_noon`` (contrôle qualité ROB piloté par seuil éditable).
Les helpers ``_setup_leg`` / ``_ev`` restent partagés avec d'autres suites.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.leg import Leg
from app.models.mrv import MRVEvent
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.mrv_compute import recompute_leg


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(
        id=1,
        leg_code="1CFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    db.add(leg)
    await db.flush()
    return leg


def _ev(leg_id, t, **kw):
    return MRVEvent(
        leg_id=leg_id,
        event_kind=kw.pop("kind", "noon_consumption"),
        recorded_at=t,
        fuel_type="MDO",
        **kw,
    )


# ─────────────────── MRV-04/05 — compteurs + qualité (moteur inerte) ────────────


@pytest.mark.asyncio
async def test_recompute_me_ae_rob_from_counters(db):
    await _setup_leg(db)
    t0 = datetime(2026, 4, 2, 12, tzinfo=UTC)
    # Compteurs en m³, densité 0.845 t/m³.
    db.add(
        _ev(
            1,
            t0,
            port_me_do_counter=Decimal(100),
            stbd_me_do_counter=Decimal(100),
            fwd_gen_do_counter=Decimal(50),
            aft_gen_do_counter=Decimal(50),
        )
    )
    db.add(
        _ev(
            1,
            t0 + timedelta(days=1),
            port_me_do_counter=Decimal(105),
            stbd_me_do_counter=Decimal(105),
            fwd_gen_do_counter=Decimal(52),
            aft_gen_do_counter=Decimal(52),
        )
    )
    await db.flush()

    await recompute_leg(db, 1)
    evs = list(
        (
            await db.execute(MRVEvent.__table__.select().order_by(MRVEvent.__table__.c.recorded_at))
        ).fetchall()
    )
    e2 = evs[1]
    # ME = (5+5)*0.845 = 8.45 ; AE = (2+2)*0.845 = 3.38 ; total = 11.83
    assert float(e2.me_consumption_t) == pytest.approx(8.45, abs=0.001)
    assert float(e2.ae_consumption_t) == pytest.approx(3.38, abs=0.001)
    assert float(e2.total_consumption_t) == pytest.approx(11.83, abs=0.001)
    assert e2.quality_status == "ok"


@pytest.mark.asyncio
async def test_quality_error_on_counter_decrease(db):
    await _setup_leg(db)
    t0 = datetime(2026, 4, 2, 12, tzinfo=UTC)
    db.add(
        _ev(
            1,
            t0,
            port_me_do_counter=Decimal(100),
            stbd_me_do_counter=Decimal(100),
            fwd_gen_do_counter=Decimal(50),
            aft_gen_do_counter=Decimal(50),
        )
    )
    db.add(
        _ev(
            1,
            t0 + timedelta(days=1),
            port_me_do_counter=Decimal(95),  # baisse → erreur
            stbd_me_do_counter=Decimal(105),
            fwd_gen_do_counter=Decimal(52),
            aft_gen_do_counter=Decimal(52),
        )
    )
    await db.flush()
    await recompute_leg(db, 1)
    evs = list(
        (
            await db.execute(MRVEvent.__table__.select().order_by(MRVEvent.__table__.c.recorded_at))
        ).fetchall()
    )
    assert evs[1].quality_status == "error"
    assert "en baisse" in (evs[1].quality_notes or "")


# ─────────────── MRV-06 — le seuil éditable pilote le contrôle qualité (sync) ────


async def _noon(db, leg, t, *, rob_l, consumed_l=0.0):
    from app.models.noon_report import NoonReport

    noon = NoonReport(
        leg_id=leg.id,
        recorded_at=t,
        latitude=49.0,
        longitude=-1.0,
        fuel_consumed_24h_l=consumed_l,
        rob_fuel_l=rob_l,
    )
    db.add(noon)
    await db.flush()
    return noon


@pytest.mark.asyncio
async def test_sync_quality_uses_editable_deviation_threshold(db):
    """MRV-06 : un seuil de déviation abaissé met l'event de sync en warning.

    Écart ≈ 1,01 t : « ok » au seuil par défaut (2 t), « warning » à 0,5 t.
    """
    from app.services.mrv_sync import ensure_from_noon

    leg = await _setup_leg(db)
    base = datetime(2026, 4, 2, tzinfo=UTC)
    # Point de référence ROB antérieur (10 000 L).
    db.add(_ev(leg.id, base, rob_l=Decimal("10000")))
    await db.flush()
    # Noon déclarant 8 800 L sans conso → écart calculé 1,2 kL ≈ 1,01 t (×0,845).
    noon = await _noon(db, leg, base + timedelta(hours=24), rob_l=8800.0, consumed_l=0.0)

    # Seuil par défaut (2 t) → conforme.
    ev = await ensure_from_noon(db, noon)
    assert ev.quality_status == "ok"
    assert "≤ 2 t" in (ev.quality_notes or "")


@pytest.mark.asyncio
async def test_sync_quality_warns_when_threshold_lowered(db):
    from app.models.mrv import MRVParameter
    from app.services.mrv_sync import ensure_from_noon

    leg = await _setup_leg(db)
    # Seuil abaissé à 0,5 t via le paramètre éditable.
    db.add(MRVParameter(name="mdo_admissible_deviation", value=Decimal("0.5"), unit="t"))
    base = datetime(2026, 4, 2, tzinfo=UTC)
    db.add(_ev(leg.id, base, rob_l=Decimal("10000")))
    await db.flush()
    noon = await _noon(db, leg, base + timedelta(hours=24), rob_l=8800.0, consumed_l=0.0)

    ev = await ensure_from_noon(db, noon)
    assert ev.quality_status == "warning"
    assert "> 0.5 t" in (ev.quality_notes or "")
