"""Planning P0 — reprise (PLN-01 brochure PDF, PLN-03 export CSV)."""

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


async def _setup(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Vessel(id=2, code="ART", name="Artemis"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 3, 1, tzinfo=UTC)
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
            eta=base + timedelta(days=20),
            status="planned",
            is_bookable=True,
        )
    )
    db.add(
        Leg(
            id=2,
            leg_code="2AFRBR6",
            vessel_id=2,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=base + timedelta(days=5),
            eta_ref=base + timedelta(days=25),
            etd=base + timedelta(days=5),
            eta=base + timedelta(days=25),
            status="planned",
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_planning_csv_export(db, staff_user):
    from app.routers.planning_router import planning_export_csv

    await _setup(db)
    resp = await planning_export_csv(_Req(), vessel_id=None, year=2026, db=db, user=staff_user)
    assert resp.media_type == "text/csv"
    text = resp.body.decode()
    header = text.splitlines()[0]
    assert "leg_code" in header and "etd" in header and "pol_locode" in header
    assert "1CFRBR6" in text and "2AFRBR6" in text
    assert "FRFEC" in text


@pytest.mark.asyncio
async def test_planning_csv_filtered_by_vessel(db, staff_user):
    from app.routers.planning_router import planning_export_csv

    await _setup(db)
    resp = await planning_export_csv(_Req(), vessel_id=1, year=2026, db=db, user=staff_user)
    text = resp.body.decode()
    assert "1CFRBR6" in text
    assert "2AFRBR6" not in text  # filtré sur le navire 1


@pytest.mark.asyncio
async def test_planning_brochure_pdf_renders(db, staff_user):
    pytest.importorskip("weasyprint")
    from app.routers.planning_router import planning_commercial_pdf

    await _setup(db)
    resp = await planning_commercial_pdf(
        _Req(),
        vessel_id=None,
        year=2026,
        lang="fr",
        group_by="chrono",
        db=db,
        user=staff_user,
    )
    assert resp.media_type == "application/pdf"
    assert len(resp.body) > 500


@pytest.mark.asyncio
async def test_planning_brochure_groups_by_destination(db, staff_user):
    """La vue groupée par destination construit bien les groupes (logique pure)."""
    from app.routers.planning_router import _planning_rows

    await _setup(db)
    rows, vmap, pmap = await _planning_rows(db, vessel_id=None, year=2026)
    assert len(rows) == 2
    # les deux legs vont à Santos → un seul bucket destination
    dests = {r["pod"].name for r in rows}
    assert dests == {"Santos"}
