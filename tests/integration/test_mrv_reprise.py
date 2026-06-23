"""MRV P0 — reprise (A1 hybride : MRV-01..07) : tests d'intégration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.leg import Leg
from app.models.mrv import MRVEvent
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.mrv_compute import recompute_leg, resolve_density


class _Req:
    def __init__(self, form: dict | None = None):
        self._form = dict(form or {})
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def form(self):
        return self._form


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


# ─────────────────────── MRV-04/05 — compteurs + qualité ───────────────────────


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


# ─────────────────────────── MRV-03 — edit/delete ───────────────────────────


@pytest.mark.asyncio
async def test_event_edit_and_delete(db, staff_user):
    from app.routers.mrv_router import add_event, delete_event, edit_event

    await _setup_leg(db)
    resp = await add_event(
        1,
        _Req(
            form={
                "event_kind": "noon_consumption",
                "recorded_at": "2026-04-02T12:00:00",
                "fuel_mass_t": "5.5",
                "distance_nm": "120",
            }
        ),
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    ev = (await db.execute(MRVEvent.__table__.select())).fetchone()
    assert float(ev.total_consumption_t) == pytest.approx(5.5)

    await edit_event(ev.id, _Req(form={"distance_nm": "150"}), db=db, user=staff_user)
    refreshed = await db.get(MRVEvent, ev.id)
    assert float(refreshed.distance_nm) == 150.0

    resp = await delete_event(ev.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(MRVEvent, ev.id)) is None


# ─────────────────────────── MRV-01 — DNV 18 colonnes ───────────────────────


@pytest.mark.asyncio
async def test_dnv_export_18_columns_with_imo(db, staff_user):
    from app.routers.mrv_router import export_dnv_csv

    await _setup_leg(db)
    db.add(
        _ev(
            1,
            datetime(2026, 4, 2, 12, tzinfo=UTC),
            fuel_mass_t=Decimal("5.5"),
            distance_nm=Decimal("120"),
            lat_deg=49,
            lat_min=Decimal("30.5"),
            lat_ns="N",
        )
    )
    await db.flush()
    await recompute_leg(db, 1)

    resp = await export_dnv_csv(db=db, user=staff_user)
    text = resp.body.decode()
    header = text.splitlines()[0]
    assert len(header.split(",")) == 18
    assert "IMO" in header
    assert "9876543" in text  # IMO renseigné (correctif)
    assert "1CFRBR6" not in header  # leg_code n'est plus une colonne (format Veracity)


# ─────────────────────────── MRV-02 — Carbon PDF + blocage ───────────────────


@pytest.mark.asyncio
async def test_carbon_pdf_renders_when_clean(db, staff_user):
    from app.routers.mrv_router import export_carbon_report_pdf

    await _setup_leg(db)
    db.add(_ev(1, datetime(2026, 4, 2, 12, tzinfo=UTC), fuel_mass_t=Decimal("5.5")))
    await db.flush()
    await recompute_leg(db, 1)
    resp = await export_carbon_report_pdf(db=db, user=staff_user)
    assert resp.media_type == "application/pdf"
    assert len(resp.body) > 500


@pytest.mark.asyncio
async def test_carbon_pdf_blocked_on_quality_error(db, staff_user):
    from fastapi import HTTPException

    from app.routers.mrv_router import export_carbon_report_pdf

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
            port_me_do_counter=Decimal(95),
            stbd_me_do_counter=Decimal(105),
            fwd_gen_do_counter=Decimal(52),
            aft_gen_do_counter=Decimal(52),
        )
    )
    await db.flush()
    await recompute_leg(db, 1)  # crée un event en erreur
    with pytest.raises(HTTPException) as exc:
        await export_carbon_report_pdf(db=db, user=staff_user)
    assert exc.value.status_code == 400


# ─────────────────────────────── MRV-06 — params ─────────────────────────────


@pytest.mark.asyncio
async def test_carbon_pdf_guard_is_scoped_to_vessel(db, staff_user):
    """Un navire propre produit son rapport même si un AUTRE navire est en erreur."""
    from app.routers.mrv_router import export_carbon_report_pdf

    await _setup_leg(db)  # vessel 1, leg 1
    # Navire 2 + leg 2 (propre).
    db.add(Vessel(id=2, code="ART", name="Artemis", imo_number="1234567", flag="FR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    db.add(
        Leg(
            id=2,
            leg_code="2AFRBR6",
            vessel_id=2,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=base,
            eta_ref=base + timedelta(days=20),
            etd=base,
            eta=base + timedelta(days=20),
        )
    )
    await db.flush()
    # Erreur sur le navire 1 (compteur en baisse).
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
            port_me_do_counter=Decimal(90),
            stbd_me_do_counter=Decimal(100),
            fwd_gen_do_counter=Decimal(50),
            aft_gen_do_counter=Decimal(50),
        )
    )
    # Événement propre sur le navire 2.
    db.add(_ev(2, t0, fuel_mass_t=Decimal("5.0")))
    await db.flush()
    await recompute_leg(db, 1)
    await recompute_leg(db, 2)

    # Navire 2 (propre) → rapport produit malgré l'erreur du navire 1.
    resp = await export_carbon_report_pdf(vessel_id=2, db=db, user=staff_user)
    assert resp.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_edit_event_rejects_invalid_value(db, staff_user):
    from fastapi import HTTPException

    from app.routers.mrv_router import add_event, edit_event

    await _setup_leg(db)
    await add_event(
        1,
        _Req(
            form={
                "event_kind": "noon_consumption",
                "recorded_at": "2026-04-02T12:00:00",
                "fuel_mass_t": "5.0",
            }
        ),
        db=db,
        user=staff_user,
    )
    ev = (await db.execute(MRVEvent.__table__.select())).fetchone()
    with pytest.raises(HTTPException) as exc:
        await edit_event(ev.id, _Req(form={"fuel_mass_t": "1o.5"}), db=db, user=staff_user)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_params_save_updates_density(db, staff_user):
    from app.routers.mrv_router import mrv_params_save

    resp = await mrv_params_save(
        _Req(),
        avg_mdo_density="0.900",
        mdo_admissible_deviation="1.5",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    assert await resolve_density(db) == Decimal("0.900")
