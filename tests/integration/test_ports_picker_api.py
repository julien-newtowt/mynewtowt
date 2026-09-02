"""Sélecteur de ports du formulaire de leg — API côté serveur.

Bug du 2026-09-02 : « Da Nang VNDAD existe bien, mais n'est pas disponible
dans le moteur de recherche. Les filtres sont incomplets. »

Cause : `leg-cascade.js` rapatriait le référentiel entier
(`/ports/search?limit=10000`) et filtrait dans le navigateur. Passé 10 000
ports en base, la requête — triée par pays — **tronquait silencieusement** :
tout ce qui suivait `JP` disparaissait (123 pays, dont VN, NL, US, RE). La
cascade Zone/Pays/Port et la recherche libre dérivant du même payload, les
trois symptômes n'avaient qu'une cause.

Correctif : recherche et cascade requêtées au serveur, `limit` bornée.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.port import Port
from app.routers.api_v1_router import (
    PORTS_SEARCH_MAX_LIMIT,
    port_by_id,
    ports_countries,
    ports_search,
)


async def _seed(db):
    """Un port par pays échelonné sur l'alphabet, plus le cas Da Nang."""
    rows = [
        ("BRSSO", "São Sebastião", "BR", -23.8, -45.4),
        ("FRFEC", "Fécamp", "FR", 49.7594, 0.3742),
        ("JPTYO", "Tokyo", "JP", 35.65, 139.75),
        ("NLRTM", "Rotterdam", "NL", 51.9167, 4.5),
        ("RELPT", "Le Port", "RE", -20.9333, 55.3167),
        ("USNYC", "New York", "US", 40.71, -74.0),
        ("VNDAD", "Da Nang", "VN", 16.0678, 108.2208),
        ("VNSGN", "Ho Chi Minh City", "VN", 10.7667, 106.6667),
    ]
    for locode, name, country, lat, lon in rows:
        db.add(
            Port(
                locode=locode,
                name=name,
                country=country,
                latitude=lat,
                longitude=lon,
                source="unlocode-improved",
            )
        )
    # Port inactif et port sans coordonnées : ni l'un ni l'autre n'est
    # sélectionnable (le second casserait la distance théorique).
    db.add(
        Port(
            locode="XXOFF",
            name="Désactivé",
            country="FR",
            latitude=1.0,
            longitude=1.0,
            is_active=False,
        )
    )
    db.add(Port(locode="XXNOC", name="Sans position", country="FR"))
    await db.flush()


# ───────────────────────────── recherche libre ─────────────────────────────


@pytest.mark.asyncio
async def test_search_finds_the_port_that_was_missing(db):
    """Le cas exact remonté : Da Nang trouvable par nom comme par LOCODE."""
    await _seed(db)
    by_name = await ports_search(q="da nang", db=db)
    by_locode = await ports_search(q="VNDAD", db=db)
    assert [p["locode"] for p in by_name] == ["VNDAD"]
    assert [p["locode"] for p in by_locode] == ["VNDAD"]
    assert by_name[0]["name"] == "Da Nang"
    assert by_name[0]["latitude"] is not None  # l'aperçu d'ETA en a besoin


@pytest.mark.asyncio
async def test_search_is_case_insensitive_and_partial(db):
    await _seed(db)
    assert [p["locode"] for p in await ports_search(q="ROTTER", db=db)] == ["NLRTM"]
    assert [p["locode"] for p in await ports_search(q="nang", db=db)] == ["VNDAD"]


@pytest.mark.asyncio
async def test_search_excludes_inactive_and_ungeolocated_ports(db):
    await _seed(db)
    found = {p["locode"] for p in await ports_search(q="XX", limit=50, db=db)}
    assert found == set()


@pytest.mark.asyncio
async def test_search_limit_is_clamped(db):
    """`limit=10000` ne doit plus être un moyen d'exporter le référentiel."""
    await _seed(db)
    rows = await ports_search(limit=10000, db=db)
    assert len(rows) <= PORTS_SEARCH_MAX_LIMIT
    # Et une limite absurde ne renvoie pas zéro ligne.
    assert len(await ports_search(limit=0, db=db)) == 1
    assert len(await ports_search(limit=-5, db=db)) == 1


@pytest.mark.asyncio
async def test_search_by_country_returns_every_port_of_that_country(db):
    """La liste « Port » de la cascade : tous les ports du pays choisi."""
    await _seed(db)
    rows = await ports_search(country="vn", limit=PORTS_SEARCH_MAX_LIMIT, db=db)
    assert [p["locode"] for p in rows] == ["VNDAD", "VNSGN"]


# ──────────────────────── cascade Zone → Pays → Port ────────────────────────


@pytest.mark.asyncio
async def test_countries_endpoint_covers_every_country_with_a_usable_port(db):
    """Aucun pays ne doit manquer : c'est ce que « filtres incomplets » disait."""
    await _seed(db)
    rows = await ports_countries(db=db)
    assert {r["country"] for r in rows} == {"BR", "FR", "JP", "NL", "RE", "US", "VN"}
    by_country = {r["country"]: r for r in rows}
    assert by_country["VN"]["port_count"] == 2
    assert by_country["FR"]["port_count"] == 1  # inactif et sans position exclus


@pytest.mark.asyncio
async def test_countries_carry_their_zone_from_the_iso_table(db):
    """La zone vient du serveur, plus d'une carte codée en dur côté navigateur."""
    await _seed(db)
    zones = {r["country"]: r["zone"] for r in await ports_countries(db=db)}
    assert zones == {
        "FR": "Europe",
        "NL": "Europe",
        "RE": "Afrique",
        "BR": "Amériques",
        "US": "Amériques",
        "JP": "Asie",
        "VN": "Asie",
    }
    assert "Autre" not in zones.values()


@pytest.mark.asyncio
async def test_countries_are_sorted_by_business_zone_order(db):
    """Europe en tête (base de la flotte), puis pays alphabétiques."""
    await _seed(db)
    rows = await ports_countries(db=db)
    assert [(r["zone"], r["country"]) for r in rows] == [
        ("Europe", "FR"),
        ("Europe", "NL"),
        ("Afrique", "RE"),
        ("Amériques", "BR"),
        ("Amériques", "US"),
        ("Asie", "JP"),
        ("Asie", "VN"),
    ]


# ──────────────────────────── port par identifiant ──────────────────────────


@pytest.mark.asyncio
async def test_port_by_id_returns_the_coordinates(db):
    """Une suggestion de séquence désigne le POL par son id ; l'aperçu d'ETA
    a besoin de sa position."""
    await _seed(db)
    port = (await ports_search(q="VNDAD", db=db))[0]
    fetched = await port_by_id(port_id=port["id"], db=db)
    assert fetched["locode"] == "VNDAD"
    assert fetched["latitude"] == pytest.approx(16.0678)


@pytest.mark.asyncio
async def test_port_by_id_404_on_unknown_or_inactive(db):
    from fastapi import HTTPException

    await _seed(db)
    with pytest.raises(HTTPException) as ei:
        await port_by_id(port_id=999999, db=db)
    assert ei.value.status_code == 404

    inactive = (await db.execute(select(Port).where(Port.locode == "XXOFF"))).scalar_one()
    with pytest.raises(HTTPException) as ei:
        await port_by_id(port_id=inactive.id, db=db)
    assert ei.value.status_code == 404
