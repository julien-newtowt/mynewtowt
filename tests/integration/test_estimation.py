"""Estimation tarifaire — extranet client, demande publique, conversion (lot 6).

Le point de sécurité du module : **le tarif négocié ne sort jamais vers
quelqu'un dont l'identité n'a pas été établie par un opérateur**. Ces tests
verrouillent les deux versants de cette règle — ce que le client authentifié
peut estimer, et ce que le visiteur public n'obtient pas.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.client_account import ClientAccount
from app.models.commercial import Client, RateGrid, RateGridLine, RateOffer
from app.models.leg import Leg
from app.models.port import Port
from app.models.quote import Quote
from app.models.vessel import Vessel
from app.services.estimation import (
    EstimationError,
    assert_route_is_covered,
    convert_to_offer,
    ensure_prospect,
    routes_for_client,
)


async def _referentiel(db) -> None:
    db.add_all(
        [
            Port(id=1, locode="FRLEH", name="Le Havre", country="FR"),
            Port(id=2, locode="BRSSZ", name="Santos", country="BR"),
            Port(id=3, locode="PTLIS", name="Lisbonne", country="PT"),
            Vessel(id=1, code="ANE", name="Anemos", capacity_palettes=500),
        ]
    )
    await db.flush()


async def _client_with_grid(
    db, *, name: str, pol="FRLEH", pod="BRSSZ", base_rate="300.00", status="active"
) -> Client:
    client = Client(name=name, client_type="shipper")
    db.add(client)
    await db.flush()
    grid = RateGrid(
        reference=f"RG-2026-{client.id:04d}",
        client_id=client.id,
        status=status,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    db.add(grid)
    await db.flush()
    db.add(
        RateGridLine(
            grid_id=grid.id,
            pol_locode=pol,
            pod_locode=pod,
            distance_nm=Decimal("5000"),
            nav_days=Decimal("26.042"),
            opex_daily=Decimal("12000"),
            base_rate=Decimal(base_rate),
        )
    )
    await db.flush()
    return client


async def _leg(db, *, pol_id=1, pod_id=2, code="1AFRBR6") -> Leg:
    leg = Leg(
        leg_code=code,
        vessel_id=1,
        departure_port_id=pol_id,
        arrival_port_id=pod_id,
        etd_ref=datetime(2026, 9, 1, tzinfo=UTC),
        eta_ref=datetime(2026, 9, 25, tzinfo=UTC),
        etd=datetime(2026, 9, 1, tzinfo=UTC),
        eta=datetime(2026, 9, 25, tzinfo=UTC),
    )
    db.add(leg)
    await db.flush()
    return leg


# ─────────────── Catalogue de routes : borné aux grilles du client ───────────


@pytest.mark.asyncio
async def test_client_only_sees_routes_covered_by_his_own_grid(db):
    await _referentiel(db)
    mine = await _client_with_grid(db, name="Mon client")
    await _client_with_grid(db, name="Autre client", pol="FRLEH", pod="PTLIS")

    routes = await routes_for_client(db, mine.id, on_date=date(2026, 6, 1))
    assert [(r["pol_locode"], r["pod_locode"]) for r in routes] == [("FRLEH", "BRSSZ")]


@pytest.mark.asyncio
async def test_no_grid_means_no_route_rather_than_a_generic_one(db):
    """Sans grille rattachée, on ne propose rien — pas un tarif générique."""
    await _referentiel(db)
    assert await routes_for_client(db, None) == []

    orphan = Client(name="Sans grille", client_type="shipper")
    db.add(orphan)
    await db.flush()
    assert await routes_for_client(db, orphan.id) == []


@pytest.mark.asyncio
async def test_expired_grid_is_not_offered(db):
    await _referentiel(db)
    client = await _client_with_grid(db, name="Client", status="draft")
    assert await routes_for_client(db, client.id, on_date=date(2026, 6, 1)) == []


@pytest.mark.asyncio
async def test_estimating_outside_your_grid_is_refused(db):
    """Sans ce contrôle, la résolution retomberait sur la grille par défaut."""
    await _referentiel(db)
    client = await _client_with_grid(db, name="Client")

    # Route couverte : passe.
    await assert_route_is_covered(
        db, commercial_client_id=client.id, pol_locode="FRLEH", pod_locode="BRSSZ",
        on_date=date(2026, 6, 1),
    )
    # Route non couverte : refusée, avec un message actionnable.
    with pytest.raises(EstimationError, match="grille"):
        await assert_route_is_covered(
            db, commercial_client_id=client.id, pol_locode="FRLEH", pod_locode="PTLIS",
            on_date=date(2026, 6, 1),
        )


@pytest.mark.asyncio
async def test_a_client_cannot_reach_another_clients_route(db):
    """Le cœur de l'isolation : la route d'autrui est refusée, pas servie."""
    await _referentiel(db)
    mine = await _client_with_grid(db, name="Mon client")
    await _client_with_grid(db, name="Concurrent", pol="FRLEH", pod="PTLIS")

    with pytest.raises(EstimationError):
        await assert_route_is_covered(
            db, commercial_client_id=mine.id, pol_locode="FRLEH", pod_locode="PTLIS",
            on_date=date(2026, 6, 1),
        )


# ────────────────────────── Fiches prospect ──────────────────────────


@pytest.mark.asyncio
async def test_public_request_creates_a_prospect(db):
    prospect = await ensure_prospect(
        db, company="Torréfaction du Nord", contact_name="Léa Martin",
        email="lea@torrefaction-nord.fr",
    )
    assert prospect is not None
    assert prospect.is_prospect is True
    assert prospect.prospect_source == "estimation_publique"
    assert prospect.name == "Torréfaction du Nord"


@pytest.mark.asyncio
async def test_prospect_creation_is_idempotent_and_never_downgrades_a_client(db):
    existing = Client(
        name="Client établi", client_type="freight_forwarder",
        contact_email="ops@etabli.fr", is_prospect=False,
    )
    db.add(existing)
    await db.flush()

    again = await ensure_prospect(db, company="Autre nom", contact_name=None, email="ops@etabli.fr")
    assert again.id == existing.id
    assert again.is_prospect is False  # un client ne redevient pas prospect
    assert again.name == "Client établi"  # ni renommé par une saisie publique


@pytest.mark.asyncio
async def test_no_email_means_no_prospect_record(db):
    """Une fiche sans moyen de recontact n'a aucune valeur et pollue le référentiel."""
    assert await ensure_prospect(db, company="Anonyme", contact_name=None, email=None) is None
    assert await ensure_prospect(db, company="Anonyme", contact_name=None, email="  ") is None


# ─────────────────────── Conversion en offre ───────────────────────


async def _estimation(db, client: Client, *, palettes=150) -> Quote:
    quote = Quote(
        reference="DEV-2026-ABCDEF123456",
        status="issued",
        origin="extranet",
        pol_locode="FRLEH",
        pod_locode="BRSSZ",
        commercial_client_id=client.id,
        palettes_total=palettes,
    )
    db.add(quote)
    await db.flush()
    return quote


@pytest.mark.asyncio
async def test_conversion_creates_an_offer_priced_on_the_current_grid(db):
    """Le prix est recalculé, pas recopié : une estimation ancienne peut être périmée."""
    await _referentiel(db)
    client = await _client_with_grid(db, name="Client")
    leg = await _leg(db)
    quote = await _estimation(db, client, palettes=150)

    offer = await convert_to_offer(db, quote, leg_id=leg.id, actor_name="Yasmin")

    assert isinstance(offer, RateOffer)
    assert offer.client_id == client.id
    assert offer.leg_id == leg.id
    assert offer.grid_id is not None
    assert offer.status == "en_cours"
    assert offer.estimated_palettes == 150
    # 150 palettes → palier « De 100 à 300 » (coeff 0.90) sur base 300.
    assert offer.proposed_rate_eur == Decimal("270.00")
    assert quote.converted_offer_id == offer.id
    assert quote.status == "accepted"


@pytest.mark.asyncio
async def test_conversion_is_refused_twice(db):
    await _referentiel(db)
    client = await _client_with_grid(db, name="Client")
    leg = await _leg(db)
    quote = await _estimation(db, client)

    await convert_to_offer(db, quote, leg_id=leg.id)
    with pytest.raises(EstimationError, match="déjà transformée"):
        await convert_to_offer(db, quote, leg_id=leg.id)


@pytest.mark.asyncio
async def test_unqualified_prospect_cannot_be_converted(db):
    """Une demande publique non rattachée doit d'abord être qualifiée."""
    await _referentiel(db)
    leg = await _leg(db)
    quote = Quote(
        reference="DEV-2026-PUBLIC000000",
        status="issued",
        origin="public_request",
        pol_locode="FRLEH",
        pod_locode="BRSSZ",
        palettes_total=80,
    )
    db.add(quote)
    await db.flush()

    with pytest.raises(EstimationError, match="qualifiez"):
        await convert_to_offer(db, quote, leg_id=leg.id)


@pytest.mark.asyncio
async def test_conversion_records_the_offer_history(db):
    from app.services.offer_history import list_revisions

    await _referentiel(db)
    client = await _client_with_grid(db, name="Client")
    leg = await _leg(db)
    quote = await _estimation(db, client)

    offer = await convert_to_offer(db, quote, leg_id=leg.id, actor_name="Yasmin")
    revisions = await list_revisions(db, offer.id)
    assert len(revisions) == 1
    assert quote.reference in (revisions[0].comment or "")


# ─────────────── Demande publique : jamais de prix ───────────────


@pytest.mark.asyncio
async def test_public_request_carries_no_price(db):
    from app.services.quoting import create_estimation_request

    await _referentiel(db)
    prospect = await ensure_prospect(
        db, company="Visiteur", contact_name=None, email="visiteur@example.org"
    )
    quote = await create_estimation_request(
        db,
        pol_locode="FRLEH",
        pod_locode="BRSSZ",
        commercial_client=prospect,
        contact_email="visiteur@example.org",
        palettes_total=60,
        items=[("EPAL", 60)],
    )

    assert quote.origin == "public_request"
    assert quote.is_priced is False
    assert quote.total_eur == Decimal("0")
    assert quote.grid_reference is None  # aucune grille n'a été résolue


@pytest.mark.asyncio
async def test_extranet_estimation_is_priced(db):
    """Contraste : le client authentifié, lui, obtient bien un tarif."""
    from app.services.quoting import compute_grid_quote, create_quote, resolve_grid

    await _referentiel(db)
    client = await _client_with_grid(db, name="Client")
    account = ClientAccount(
        email="ops@client.fr",
        hashed_password="x",
        company_name="Client",
        is_verified=True,
        commercial_client_id=client.id,
    )
    db.add(account)
    await db.flush()

    grid, route = await resolve_grid(
        db, pol_locode="FRLEH", pod_locode="BRSSZ",
        on_date=date(2026, 6, 1), commercial_client_id=client.id,
    )
    computed = compute_grid_quote(grid, route, items=[("EPAL", 150)])
    quote = await create_quote(
        db, computed=computed, pol_locode="FRLEH", pod_locode="BRSSZ",
        client_account=account, palettes_total=150, tonnage_t=None, hazardous=False,
        items=[("EPAL", 150)],
    )
    quote.origin = "extranet"
    await db.flush()

    assert quote.is_priced is True
    assert quote.total_eur > 0
    assert quote.grid_reference == grid.reference
