"""Alimentation du référentiel de ports — upsert et hiérarchie des sources.

Le référentiel de ports est une source de vérité partagée : le planning en
tire la distance théorique de chaque leg, le commercial les routes de grille,
la vitrine les positions publiées. Un import automatique qui dégrade une
coordonnée curée se propage donc partout — d'où la hiérarchie de sources
vérifiée ici sur une vraie session DB.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.port import Port
from app.services.ports import PortRow, upsert_ports


def _row(locode: str, *, lat: float, lon: float, source: str, name: str = "Fécamp") -> PortRow:
    return PortRow(
        locode=locode,
        name=name,
        country=locode[:2],
        latitude=lat,
        longitude=lon,
        source=source,
        function_code="1-------",
    )


async def _port(db, locode: str) -> Port:
    return (await db.execute(select(Port).where(Port.locode == locode))).scalar_one()


@pytest.mark.asyncio
async def test_upsert_inserts_then_refreshes_same_source(db):
    ins, upd = await upsert_ports(db, [_row("FRFEC", lat=49.75, lon=0.383, source="unlocode")])
    assert (ins, upd) == (1, 0)
    ins, upd = await upsert_ports(db, [_row("FRFEC", lat=49.76, lon=0.374, source="unlocode")])
    assert (ins, upd) == (0, 1)
    port = await _port(db, "FRFEC")
    assert (port.latitude, port.longitude) == (49.76, 0.374)


@pytest.mark.asyncio
async def test_automatic_import_never_degrades_curated_coordinates(db):
    """Catalogue embarqué (curé à la main) vs UN/LOCODE (arrondi à la minute)."""
    await upsert_ports(db, [_row("FRFEC", lat=49.7594, lon=0.3742, source="world_ports")])
    ins, upd = await upsert_ports(
        db, [_row("FRFEC", lat=49.75, lon=0.38333, source="unlocode-improved")]
    )
    assert (ins, upd) == (0, 0)  # refusé
    port = await _port(db, "FRFEC")
    assert (port.latitude, port.longitude) == (49.7594, 0.3742)
    assert port.source == "world_ports"


@pytest.mark.asyncio
async def test_operator_correction_survives_every_refresh(db):
    """Une coordonnée corrigée dans Admin → Ports est intouchable.

    C'est la garantie qui rend l'écran de correction utile : sans elle, le
    prochain rafraîchissement automatique effacerait la saisie de l'opérateur.
    """
    await upsert_ports(db, [_row("REPDG", lat=0.0, lon=0.0, source="unlocode")])
    port = await _port(db, "REPDG")
    port.latitude, port.longitude, port.source = -20.9333, 55.3167, "manual"
    await db.flush()

    for source in ("unlocode", "unlocode-improved", "world_ports", "datagouv:default"):
        ins, upd = await upsert_ports(db, [_row("REPDG", lat=1.0, lon=2.0, source=source)])
        assert (ins, upd) == (0, 0), source
    port = await _port(db, "REPDG")
    assert (port.latitude, port.longitude) == (-20.9333, 55.3167)


@pytest.mark.asyncio
async def test_improved_coordinates_are_not_overwritten_by_the_plain_mirror(db):
    await upsert_ports(
        db, [_row("PHMNL", lat=14.590449, lon=120.980362, source="unlocode-improved")]
    )
    ins, upd = await upsert_ports(db, [_row("PHMNL", lat=14.5833, lon=120.9833, source="unlocode")])
    assert (ins, upd) == (0, 0)
    port = await _port(db, "PHMNL")
    assert port.latitude == 14.590449


@pytest.mark.asyncio
async def test_upsert_skips_rows_without_coordinates(db):
    """Un port sans position n'entre pas : il casserait la distance théorique."""
    rows = [
        PortRow(
            locode="FRXXX",
            name="Sans position",
            country="FR",
            latitude=None,  # type: ignore[arg-type]
            longitude=None,  # type: ignore[arg-type]
            source="unlocode",
        )
    ]
    assert await upsert_ports(db, rows) == (0, 0)
    assert (await db.execute(select(Port))).scalars().all() == []
