"""Distance théorique de planning — repli d'affichage et reprise (bug 2026-09-02).

Retour utilisateur : « il y a des voyages qui n'ont pas de calcul automatisé de
la distance et de la dérive » — colonnes Théorique / Écart / Allongement vides
sur /performance/navigation. Cause : ``Leg.distance_nm`` vaut ``None`` quand un
port n'avait pas de coordonnées à la création du leg, et rien ne la recalcule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.planning import create_leg, recompute_leg_distances
from app.services.voyage_track import compute_metrics, theoretical_distance_nm

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _seed(db, *, arrival_has_coords: bool):
    db.add(Vessel(id=1, code="1", name="Anemos", default_elongation=1.0))
    db.add(
        Port(id=1, locode="FRFEC", name="Fécamp", country="FR", latitude=49.7594, longitude=0.3742)
    )
    db.add(
        Port(
            id=2,
            locode="REPDG",
            name="Port des Galets",
            country="RE",
            latitude=-20.9373 if arrival_has_coords else None,
            longitude=55.2925 if arrival_has_coords else None,
        )
    )
    await db.flush()


async def _leg(db):
    return await create_leg(
        db,
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd=BASE,
        eta=BASE + timedelta(days=30),
    )


@pytest.mark.asyncio
async def test_leg_created_without_port_coordinates_has_no_distance(db):
    """Point de départ du bug : la distance n'est pas calculable, donc absente."""
    await _seed(db, arrival_has_coords=False)
    leg = await _leg(db)
    assert leg.distance_nm is None


@pytest.mark.asyncio
async def test_metrics_fall_back_to_orthodromy_at_render(db):
    """Distance non persistée mais ports géolocalisés → calculée à l'affichage.

    Sans ce repli, un leg dont la distance n'a jamais été posée affiche « — »
    en Théorique, donc « — » en Écart et en Allongement.
    """
    await _seed(db, arrival_has_coords=True)
    leg = await _leg(db)
    leg.distance_nm = None  # legs hérités / créés avant le calcul automatique
    await db.flush()
    dep = await db.get(Port, 1)
    arr = await db.get(Port, 2)

    value, is_fallback = theoretical_distance_nm(leg, dep_port=dep, arr_port=arr)
    assert value is not None and value > 5000  # Fécamp → La Réunion
    assert is_fallback is True

    positions = [
        VesselPosition(vessel_id=1, latitude=49.75, longitude=0.37, recorded_at=BASE),
        VesselPosition(
            vessel_id=1, latitude=-20.93, longitude=55.29, recorded_at=BASE + timedelta(days=30)
        ),
    ]
    metrics = compute_metrics(positions, leg, dep_port=dep, arr_port=arr)
    assert metrics.theoretical_nm is not None
    assert metrics.theoretical_is_fallback is True
    assert metrics.real_elongation is not None  # la dérive redevient calculable


@pytest.mark.asyncio
async def test_metrics_prefer_persisted_distance(db):
    """La valeur persistée reste prioritaire — le repli ne la contredit jamais."""
    await _seed(db, arrival_has_coords=True)
    leg = await _leg(db)
    leg.distance_nm = Decimal("1234.00")
    await db.flush()
    dep, arr = await db.get(Port, 1), await db.get(Port, 2)
    value, is_fallback = theoretical_distance_nm(leg, dep_port=dep, arr_port=arr)
    assert value == 1234.0
    assert is_fallback is False


@pytest.mark.asyncio
async def test_metrics_stay_empty_without_coordinates(db):
    """Aucune coordonnée = aucune distance inventée (l'audit nomme le port)."""
    await _seed(db, arrival_has_coords=False)
    leg = await _leg(db)
    dep, arr = await db.get(Port, 1), await db.get(Port, 2)
    value, is_fallback = theoretical_distance_nm(leg, dep_port=dep, arr_port=arr)
    assert value is None and is_fallback is False


@pytest.mark.asyncio
async def test_recompute_after_port_coordinates_are_filled(db):
    """Renseigner les coordonnées d'un port répare les legs qui y touchent."""
    await _seed(db, arrival_has_coords=False)
    leg = await _leg(db)
    assert leg.distance_nm is None

    port = await db.get(Port, 2)
    port.latitude, port.longitude = -20.9373, 55.2925
    await db.flush()

    changed = await recompute_leg_distances(db, port_id=2)
    assert [c[1] for c in changed] == [leg.leg_code]
    refreshed = (await db.execute(select(Leg).where(Leg.id == leg.id))).scalar_one()
    assert refreshed.distance_nm is not None and refreshed.distance_nm > 5000


@pytest.mark.asyncio
async def test_recompute_only_missing_by_default(db):
    """Par défaut on ne réécrit pas une distance déjà posée (idempotent)."""
    await _seed(db, arrival_has_coords=True)
    leg = await _leg(db)
    leg.distance_nm = Decimal("42.00")
    await db.flush()
    assert await recompute_leg_distances(db) == []
    changed = await recompute_leg_distances(db, only_missing=False)
    assert [c[1] for c in changed] == [leg.leg_code]
