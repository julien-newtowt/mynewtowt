"""Rattachement d'une opération au voyage en cours.

Le module de vente à bord rattachait ses ventes — et donc les mouvements de
caisse correspondants — au **dernier leg créé** (``ORDER BY id DESC``). Un leg
planifié la veille pour l'année suivante l'emportait sur le voyage réellement
en cours : toute analyse par voyage bâtie là-dessus aurait été fausse, et la
donnée se corrompait un peu plus à chaque vente (audit du 2026-08-27).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.planning import current_leg_id

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
async def ports(db):
    """Deux ports — les legs les exigent (clés étrangères non nullables)."""
    fecamp = Port(locode="FRFEC", name="Fécamp", country="FR")
    fortaleza = Port(locode="BRFOR", name="Fortaleza", country="BR")
    db.add_all([fecamp, fortaleza])
    await db.flush()
    return fecamp, fortaleza


async def _vessel(db, code="ANE", name="Anemos"):
    vessel = Vessel(code=code, name=name)
    db.add(vessel)
    await db.flush()
    return vessel


async def _leg(db, ports, vessel, code, etd, eta, **kw):
    pol, pod = ports
    leg = Leg(
        leg_code=code,
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=etd,
        eta_ref=eta,
        etd=etd,
        eta=eta,
        atd=kw.get("atd"),
        ata=kw.get("ata"),
        status=kw.get("status", "planned"),
    )
    db.add(leg)
    await db.flush()
    return leg


@pytest.mark.asyncio
async def test_a_leg_actually_under_way_wins(db, ports):
    """Le fait prime sur la prévision : parti, pas encore arrivé."""
    vessel = await _vessel(db)
    en_cours = await _leg(
        db,
        ports,
        vessel,
        "1AFRBR6",
        NOW - timedelta(days=5),
        NOW + timedelta(days=5),
        atd=NOW - timedelta(days=4),
        status="in_progress",
    )
    # Leg planifié **créé après**, pour l'an prochain : c'est celui que
    # l'ancienne implémentation renvoyait.
    await _leg(db, ports, vessel, "1BFRBR7", NOW + timedelta(days=300), NOW + timedelta(days=330))
    assert await current_leg_id(db, vessel.id, when=NOW) == en_cours.id


@pytest.mark.asyncio
async def test_the_forecast_window_is_used_when_nothing_has_departed(db, ports):
    vessel = await _vessel(db)
    dans_la_fenetre = await _leg(
        db, ports, vessel, "1AFRBR6", NOW - timedelta(days=2), NOW + timedelta(days=2)
    )
    await _leg(db, ports, vessel, "1BFRBR7", NOW + timedelta(days=100), NOW + timedelta(days=120))
    assert await current_leg_id(db, vessel.id, when=NOW) == dans_la_fenetre.id


@pytest.mark.asyncio
async def test_a_future_leg_is_never_returned(db, ports):
    """Le défaut d'origine : rattacher une vente d'aujourd'hui à un voyage
    qui n'a pas commencé."""
    vessel = await _vessel(db)
    await _leg(db, ports, vessel, "1AFRBR7", NOW + timedelta(days=30), NOW + timedelta(days=45))
    assert await current_leg_id(db, vessel.id, when=NOW) is None


@pytest.mark.asyncio
async def test_the_last_departed_leg_is_the_fallback(db, ports):
    """Navire à quai entre deux voyages : on rattache au dernier effectué."""
    vessel = await _vessel(db)
    await _leg(
        db,
        ports,
        vessel,
        "1AFRBR6",
        NOW - timedelta(days=60),
        NOW - timedelta(days=45),
        atd=NOW - timedelta(days=60),
        ata=NOW - timedelta(days=46),
        status="completed",
    )
    dernier = await _leg(
        db,
        ports,
        vessel,
        "1BFRBR6",
        NOW - timedelta(days=20),
        NOW - timedelta(days=5),
        atd=NOW - timedelta(days=20),
        ata=NOW - timedelta(days=6),
        status="completed",
    )
    assert await current_leg_id(db, vessel.id, when=NOW) == dernier.id


@pytest.mark.asyncio
async def test_a_cancelled_leg_is_ignored(db, ports):
    vessel = await _vessel(db)
    await _leg(
        db,
        ports,
        vessel,
        "1AFRBR6",
        NOW - timedelta(days=2),
        NOW + timedelta(days=2),
        status="cancelled",
    )
    assert await current_leg_id(db, vessel.id, when=NOW) is None


@pytest.mark.asyncio
async def test_another_vessels_leg_is_never_returned(db, ports):
    vessel = await _vessel(db)
    autre = await _vessel(db, code="GRA", name="Grain de Sail")
    await _leg(db, ports, autre, "2AFRBR6", NOW - timedelta(days=2), NOW + timedelta(days=2))
    assert await current_leg_id(db, vessel.id, when=NOW) is None


@pytest.mark.asyncio
async def test_no_leg_at_all_is_not_an_error(db, ports):
    """Un navire sans voyage doit pouvoir vendre : le rattachement est optionnel."""
    vessel = await _vessel(db)
    assert await current_leg_id(db, vessel.id, when=NOW) is None
