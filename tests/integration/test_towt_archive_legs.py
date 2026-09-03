"""Archives TOWT (ADR-014) — un leg repris de l'ancienne compagnie est un fait.

Lecture seule (édition, déplacement, suppression, déclarations refusées),
exclu de la renumérotation des codes, filtrable par origine, escale verrouillée.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.planning import (
    LegArchivedError,
    assert_leg_mutable,
    delete_leg,
    list_legs_in_window,
    renumber_vessel_year,
    update_leg,
)
from app.services.voyage_transitions import declare_arrival, declare_departure

D = datetime(2026, 1, 13, tzinfo=UTC)


async def _setup(db):
    db.add(Vessel(id=1, code="1", name="Anemos"))
    db.add(Port(id=1, locode="USNYC", name="New York", country="US"))
    db.add(Port(id=2, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=3, locode="BRSSO", name="São Sebastião", country="BR"))
    await db.flush()
    archive = Leg(
        id=1,
        leg_code="1AYF6",  # TRIP CODE TOWT d'origine, conservé
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=D,
        eta_ref=D + timedelta(days=18),
        etd=D,
        eta=D + timedelta(days=18),
        atd=D,
        ata=D + timedelta(days=18),
        status="completed",
        origin=LEG_ORIGIN_TOWT,
        voyage_completed_at=D + timedelta(days=18),
    )
    live = Leg(
        id=2,
        leg_code="1AFRBR6",
        vessel_id=1,
        departure_port_id=2,
        arrival_port_id=3,
        etd_ref=D + timedelta(days=150),
        eta_ref=D + timedelta(days=180),
        etd=D + timedelta(days=150),
        eta=D + timedelta(days=180),
    )
    db.add_all([archive, live])
    await db.flush()
    return archive, live


def test_default_origin_is_newtowt():
    leg = Leg(leg_code="X", vessel_id=1, departure_port_id=1, arrival_port_id=2)
    assert leg.origin in (None, "newtowt")  # défaut Python posé au flush
    assert leg.is_archive is False
    leg.origin = LEG_ORIGIN_TOWT
    assert leg.is_archive is True


@pytest.mark.asyncio
async def test_archive_leg_refuses_every_mutation(db):
    archive, _live = await _setup(db)
    with pytest.raises(LegArchivedError):
        assert_leg_mutable(archive)
    with pytest.raises(LegArchivedError):
        await update_leg(db, archive, etd=D + timedelta(days=1))
    with pytest.raises(LegArchivedError):
        await delete_leg(db, archive)
    with pytest.raises(LegArchivedError):
        await declare_departure(db, archive, at=D, quiet=True)
    with pytest.raises(LegArchivedError):
        await declare_arrival(db, archive, at=D + timedelta(days=1), quiet=True)
    assert archive.phase == "termine"
    assert (await db.get(Leg, 1)) is archive  # toujours là, inchangé


@pytest.mark.asyncio
async def test_renumbering_ignores_archive_codes(db):
    archive, live = await _setup(db)
    # L'archive est le premier leg 2026 par ETD : sans exclusion elle prendrait
    # le rang A et pousserait le leg vécu dans l'ERP en « 1BFRBR6 ».
    changes = await renumber_vessel_year(db, 1, 2026)
    assert changes == []
    assert archive.leg_code == "1AYF6"
    assert live.leg_code == "1AFRBR6"


@pytest.mark.asyncio
async def test_list_legs_filters_by_origin(db):
    await _setup(db)
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC)
    all_legs = await list_legs_in_window(db, date_from=start, date_to=end)
    assert {lg.id for lg in all_legs} == {1, 2}
    only_towt = await list_legs_in_window(db, date_from=start, date_to=end, origin=LEG_ORIGIN_TOWT)
    assert [lg.id for lg in only_towt] == [1]
    only_new = await list_legs_in_window(db, date_from=start, date_to=end, origin="newtowt")
    assert [lg.id for lg in only_new] == [2]


@pytest.mark.asyncio
async def test_escale_cockpit_sees_archive_as_locked(db):
    from app.routers.escale_router import _escale_locked

    archive, live = await _setup(db)
    assert _escale_locked(archive) is True
    assert _escale_locked(live) is False


@pytest.mark.asyncio
async def test_leg_filter_years_reach_back_to_first_etd(db):
    from app.services.leg_filter import build_leg_filter

    await _setup(db)
    db.add(
        Leg(
            id=3,
            leg_code="1LY1A4",
            vessel_id=1,
            departure_port_id=2,
            arrival_port_id=1,
            etd_ref=datetime(2024, 8, 9, tzinfo=UTC),
            eta_ref=datetime(2024, 9, 3, tzinfo=UTC),
            etd=datetime(2024, 8, 9, tzinfo=UTC),
            eta=datetime(2024, 9, 3, tzinfo=UTC),
            origin=LEG_ORIGIN_TOWT,
        )
    )
    await db.flush()
    # Par défaut l'archive est hors filtre (et hors fenêtre d'années) ; les
    # pages de lecture (tracking, navigation) la demandent explicitement.
    f = await build_leg_filter(db, vessel="1", year=2026)
    assert 2024 not in f["years"]
    f = await build_leg_filter(db, vessel="1", year=2026, include_archive=True)
    assert 2024 in f["years"] and 2028 in f["years"]
