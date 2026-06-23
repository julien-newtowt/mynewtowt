"""Admin P0 — reprise (ADM-01 CRUD navires, ADM-02 moteur d'alertes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.commercial import Client, Order
from app.models.escale import EscaleOperation
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


# ─────────────────────────────── ADM-01 ───────────────────────────────


@pytest.mark.asyncio
async def test_vessel_create_edit_toggle(db, staff_user):
    from app.routers.admin_router import vessel_create, vessel_edit, vessel_toggle_active

    resp = await vessel_create(
        _Req(),
        code="art",
        name="Artemis",
        vessel_class="phoenix",
        imo_number="1234567",
        flag="fr",
        dwt="1200",
        capacity_palettes="850",
        default_speed_kn="8.5",
        default_elongation="1.2",
        opex_daily_sea_eur="9000",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    v = (await db.execute(Vessel.__table__.select())).fetchone()
    assert v.code == "ART"  # normalisé majuscules
    assert v.flag == "FR"
    assert v.capacity_palettes == 850
    assert float(v.default_speed_kn) == 8.5

    await vessel_edit(
        v.id,
        _Req(),
        name="Artemis II",
        vessel_class="phoenix",
        imo_number="1234567",
        flag="FR",
        dwt="1300",
        capacity_palettes="900",
        default_speed_kn="9",
        default_elongation="1.15",
        opex_daily_sea_eur="9500",
        db=db,
        user=staff_user,
    )
    obj = await db.get(Vessel, v.id)
    assert obj.name == "Artemis II" and obj.capacity_palettes == 900

    assert obj.is_active is True
    await vessel_toggle_active(v.id, _Req(), db=db, user=staff_user)
    obj = await db.get(Vessel, v.id)
    assert obj.is_active is False


@pytest.mark.asyncio
async def test_vessel_create_rejects_duplicate_code(db, staff_user):
    from fastapi import HTTPException

    from app.routers.admin_router import vessel_create

    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await vessel_create(
            _Req(),
            code="ANE",
            name="Clone",
            vessel_class="phoenix",
            imo_number=None,
            flag=None,
            dwt=None,
            capacity_palettes=None,
            default_speed_kn=None,
            default_elongation=None,
            opex_daily_sea_eur=None,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 409


# ─────────────────────────────── ADM-02 ───────────────────────────────


async def _ports_vessels(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Vessel(id=2, code="ART", name="Artemis"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()


def _leg(leg_id, vessel_id, etd, eta, **kw):
    return Leg(
        id=leg_id,
        leg_code=f"{leg_id}X",
        vessel_id=vessel_id,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=etd,
        eta_ref=eta,
        etd=etd,
        eta=eta,
        **kw,
    )


@pytest.mark.asyncio
async def test_alerts_eta_overdue_and_late_arrival(db):
    from app.services.dashboard_alerts import compute_alerts

    await _ports_vessels(db)
    now = datetime.now(UTC)
    year = now.year
    # ETA dépassée de 48h, pas d'ATA → danger.
    db.add(_leg(1, 1, etd=now - timedelta(days=25), eta=now - timedelta(hours=48)))
    # ATA 30h après ETA → retard warning.
    eta2 = now - timedelta(days=5)
    db.add(
        _leg(2, 2, etd=now - timedelta(days=25), eta=eta2, ata=eta2 + timedelta(hours=30), atd=None)
    )
    await db.flush()

    alerts = await compute_alerts(db, year)
    fams = {a["family"] for a in alerts}
    assert "retard" in fams
    # tri par sévérité : danger en tête
    assert alerts[0]["severity"] == "danger"


@pytest.mark.asyncio
async def test_alerts_imminent_departure_without_ops(db):
    from app.services.dashboard_alerts import compute_alerts

    await _ports_vessels(db)
    now = datetime.now(UTC)
    db.add(_leg(1, 1, etd=now + timedelta(hours=12), eta=now + timedelta(days=20)))
    await db.flush()
    alerts = await compute_alerts(db, now.year)
    assert any(a["family"] == "preparation" for a in alerts)

    # Avec une opération planifiée → plus d'alerte préparation.
    db.add(EscaleOperation(leg_id=1, operation_type="technique", action="inspection"))
    await db.flush()
    alerts = await compute_alerts(db, now.year)
    assert not any(a["family"] == "preparation" for a in alerts)


@pytest.mark.asyncio
async def test_alerts_port_conflict_and_unassigned_orders(db):
    from app.services.dashboard_alerts import compute_alerts

    await _ports_vessels(db)
    now = datetime.now(UTC)
    eta = now + timedelta(days=10)
    # Deux navires différents, même port d'arrivée, ETA à <48h → conflit.
    db.add(_leg(1, 1, etd=now + timedelta(days=1), eta=eta))
    db.add(_leg(2, 2, etd=now + timedelta(days=1), eta=eta + timedelta(hours=12)))
    # Commande active non affectée.
    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    await db.flush()
    db.add(Order(reference="ORD-1", client_id=c.id, status="confirmed", leg_id=None))
    await db.flush()

    alerts = await compute_alerts(db, now.year)
    fams = {a["family"] for a in alerts}
    assert "conflit" in fams
    assert "commercial" in fams
