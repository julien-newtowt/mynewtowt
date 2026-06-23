"""Planning P1 — reprise (PLN-05 détection de retard, PLN-06 vue par port)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    state = SimpleNamespace(csrf_token="t", lang="fr")
    url = SimpleNamespace(path="/planning/by-port", query="")
    query_params: dict[str, str] = {}
    scope: dict = {"type": "http"}


# ─────────────────────────────── PLN-05 ───────────────────────────────


def test_leg_delay_detection():
    from app.services.planning import is_delayed, leg_delay_hours

    base = datetime(2026, 4, 1, tzinfo=UTC)
    on_time = SimpleNamespace(
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base + timedelta(hours=1),
        eta=base + timedelta(days=20, hours=2),
    )
    late = SimpleNamespace(
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20, hours=6),  # +6 h sur l'ETA
    )
    assert is_delayed(on_time) is False  # max 2 h < seuil 4 h
    assert is_delayed(late) is True
    assert leg_delay_hours(late) == pytest.approx(6.0)


# ─────────────────────────────── PLN-06 ───────────────────────────────


async def _setup(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 3, 1, tzinfo=UTC)
    # leg en retard (ETA +6h vs ref).
    db.add(
        Leg(
            id=1,
            leg_code="1CFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=base,
            eta_ref=base + timedelta(days=20),
            etd=base,
            eta=base + timedelta(days=20, hours=6),
            status="planned",
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_by_port_groups_departures_and_arrivals(db, staff_user):
    from app.routers.planning_router import planning_by_port

    await _setup(db)
    resp = await planning_by_port(_Req(), vessel_id=None, year=2026, db=db, user=staff_user)
    assert resp.status_code == 200
    body = resp.body.decode()
    # Fécamp apparaît comme port de départ, Santos comme arrivée, + badge retard.
    assert "FRFEC" in body and "BRSSO" in body
    assert "retard" in body
