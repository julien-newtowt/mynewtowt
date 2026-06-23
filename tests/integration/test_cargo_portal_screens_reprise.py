"""Cargo P1 — reprise (CARGO-10 suivi voyage + CARGO-11 guide & fiche navire).

Les routes rendent un template : appeler la coroutine via ``TemplateResponse``
rend l'arbre Jinja complet (base → _layout → page) et lève sur toute erreur de
template — le test vaut donc validation de rendu, pas seulement de routage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.claim import VesselPosition
from app.models.commercial import Client, Order
from app.models.leg import Leg
from app.models.packing_list import PackingList
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/p/x/voyage")
    state = SimpleNamespace(notif_count=0, newtowt_agent_enabled=True)


async def _pl(db, *, with_leg: bool = True, with_position: bool = False):
    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    if with_leg:
        db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
        db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
        db.add(Port(id=2, locode="USNYC", name="New York", country="US"))
        await db.flush()
        base = datetime(2026, 4, 1, tzinfo=UTC)
        db.add(
            Leg(
                id=1,
                leg_code="1AFRUS6",
                vessel_id=1,
                departure_port_id=1,
                arrival_port_id=2,
                etd_ref=base,
                eta_ref=base + timedelta(days=20),
                etd=base + timedelta(days=1),
                eta=base + timedelta(days=21),
                atd=base + timedelta(days=1, hours=2),
            )
        )
        if with_position:
            db.add(
                VesselPosition(
                    vessel_id=1,
                    recorded_at=base + timedelta(days=3),
                    latitude=45.123,
                    longitude=-12.456,
                    sog_kn=7.8,
                    cog_deg=270.0,
                )
            )
    await db.flush()
    o = Order(reference="ORD-2026-0001", client_id=c.id, leg_id=1 if with_leg else None)
    db.add(o)
    await db.flush()
    pl = PackingList(order_id=o.id, status="draft")
    db.add(pl)
    await db.flush()
    return pl


# ─────────────────────────────── CARGO-10 ───────────────────────────────


@pytest.mark.asyncio
async def test_portal_voyage_renders_with_position(db):
    from app.routers.cargo_portal_router import portal_voyage

    pl = await _pl(db, with_position=True)
    resp = await portal_voyage(pl.token, _Req(), db=db)
    assert resp.status_code == 200
    assert resp.template.name == "portal/voyage.html"
    assert resp.context["leg"] is not None
    assert resp.context["last_position"] is not None


@pytest.mark.asyncio
async def test_portal_voyage_renders_without_leg(db):
    from app.routers.cargo_portal_router import portal_voyage

    pl = await _pl(db, with_leg=False)
    resp = await portal_voyage(pl.token, _Req(), db=db)
    assert resp.status_code == 200
    assert resp.context["leg"] is None
    assert resp.context["last_position"] is None


# ─────────────────────────────── CARGO-11 ───────────────────────────────


@pytest.mark.asyncio
async def test_portal_guide_renders(db):
    from app.routers.cargo_portal_router import portal_guide

    pl = await _pl(db)
    resp = await portal_guide(pl.token, _Req(), db=db)
    assert resp.status_code == 200
    assert resp.template.name == "portal/guide.html"


@pytest.mark.asyncio
async def test_portal_vessel_renders(db):
    from app.routers.cargo_portal_router import portal_vessel

    pl = await _pl(db)
    resp = await portal_vessel(pl.token, _Req(), db=db)
    assert resp.status_code == 200
    assert resp.template.name == "portal/vessel.html"
    assert resp.context["vessel"].name == "Anemos"
