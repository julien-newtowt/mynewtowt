"""COM-12 / COM-13 — prix annoncé, coût calculé, marge dérivée.

Couvre l'inversion arbitrée le 2026-09-04 (ADR-015) :

* le commercial **annonce un prix**, le logiciel **calcule un coût** et en
  **dérive** la marge ;
* ``is_manual`` se relit « prix confirmé » — un recalcul de coût ne déplace
  jamais un prix confirmé ;
* une route porte son **unité de vente** (palette / tonne), et une cotation au
  poids sans tonnage est **refusée** plutôt qu'approximée ;
* ``cost_rate = None`` signifie « non calculable », jamais zéro ;
* une **commande** ne se crée plus ex nihilo (COM-13) ;
* supprimer une pièce **émise** est réservé à l'administrateur, et refusé tant
  qu'une pièce s'y adosse.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.commercial import (
    RATE_UNIT_PALETTE,
    RATE_UNIT_TONNE,
    Client,
    Order,
    RateGrid,
    RateGridLine,
    RateOffer,
)
from app.models.finance import OpexParameter
from app.models.port import Port
from app.models.quote import Quote
from app.models.vessel import Vessel
from app.services.quoting import (
    QuotingError,
    compute_grid_quote,
    compute_route_economics,
    route_cost_per_tonne,
    route_cost_rate,
    suggested_price,
)

from .conftest import FakeRequest

_OPEX = Decimal("12000")


def _non_admin():
    return SimpleNamespace(id=2, full_name="Commercial", username="com", role="commercial")


async def _referentials(db, *, dwt: float | None = 1100.0):
    db.add(
        Vessel(
            id=1,
            code="ANE",
            name="Anemos",
            imo_number="9876543",
            flag="FR",
            capacity_palettes=978,
            dwt=dwt,
            is_active=True,
        )
    )
    db.add_all(
        [
            Port(id=1, locode="FRFEC", name="Fécamp", country="FR"),
            Port(id=2, locode="BRSSO", name="Santos", country="BR"),
        ]
    )
    db.add(OpexParameter(parameter_name="opex_daily_sea", parameter_value=_OPEX))
    await db.flush()


async def _grid(db, *, client_id=None, status="draft"):
    grid = RateGrid(
        reference="RG-2026-0001",
        client_id=client_id,
        status=status,
        valid_from=date(2026, 1, 1),
        is_default=client_id is None,
    )
    db.add(grid)
    await db.flush()
    return grid


# ─────────────────────── Coût, prix proposé, marge ───────────────────────


def test_margin_is_derived_from_price_over_cost():
    """La marge se lit **sur le prix de vente**, pas sur le coût."""
    route = RateGridLine(
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=Decimal("4500"),
        nav_days=Decimal("23.4"),
        opex_daily=_OPEX,
        base_rate=Decimal("400.00"),
        cost_rate=Decimal("300.00"),
        rate_unit=RATE_UNIT_PALETTE,
    )
    assert route.margin_eur == Decimal("100.00")
    assert route.margin_pct == Decimal("25.0")
    assert route.is_below_cost is False
    assert route.rate_unit_label == "€/palette"


def test_selling_below_cost_is_flagged():
    route = RateGridLine(
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=Decimal("4500"),
        nav_days=Decimal("23.4"),
        opex_daily=_OPEX,
        base_rate=Decimal("250.00"),
        cost_rate=Decimal("300.00"),
    )
    assert route.margin_eur == Decimal("-50.00")
    assert route.is_below_cost is True


def test_unknown_cost_yields_no_margin_and_no_alert():
    """Un coût inconnu n'est pas un coût nul : la marge est absente, pas fausse.

    Et l'absence d'information n'est pas une alerte de vente à perte — c'est le
    même arbitrage que ``schengen_status = indetermine`` côté équipage.
    """
    route = RateGridLine(
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=Decimal("4500"),
        nav_days=Decimal("23.4"),
        opex_daily=_OPEX,
        base_rate=Decimal("250.00"),
        cost_rate=None,
    )
    assert route.margin_eur is None
    assert route.margin_pct is None
    assert route.is_below_cost is False


def test_suggested_price_covers_the_target_margin():
    cost = Decimal("300.00")
    price = suggested_price(cost)
    assert price == Decimal("400.00")  # 300 / (1 - 0,25)
    # La marge du prix proposé vaut bien la marge cible.
    assert ((price - cost) / price * 100).quantize(Decimal("0.1")) == Decimal("25.0")


def test_suggested_price_is_none_without_a_cost():
    """Proposer un prix sans coût reviendrait à inventer la marge annoncée."""
    assert suggested_price(None) is None


def test_cost_per_tonne_is_none_without_a_deadweight():
    assert route_cost_per_tonne(_OPEX, Decimal("10"), None) is None
    assert route_cost_per_tonne(_OPEX, Decimal("10"), Decimal("0")) is None
    assert route_cost_per_tonne(_OPEX, Decimal("10"), Decimal("1000")) == Decimal("120.00")


@pytest.mark.asyncio
async def test_route_economics_returns_cost_in_the_route_unit(db):
    await _referentials(db)
    _d, nav_days, opex, cost_pal = await compute_route_economics(
        db, pol_locode="FRFEC", pod_locode="BRSSO"
    )
    assert opex == _OPEX
    assert cost_pal == route_cost_rate(_OPEX, nav_days)

    _d, nav_days_t, _o, cost_t = await compute_route_economics(
        db, pol_locode="FRFEC", pod_locode="BRSSO", rate_unit=RATE_UNIT_TONNE
    )
    # Port en lourd de la flotte (1100 t) à défaut de navire de référence.
    assert cost_t == route_cost_per_tonne(_OPEX, nav_days_t, Decimal("1100"))
    assert cost_t != cost_pal


@pytest.mark.asyncio
async def test_route_cost_at_tonne_is_none_when_fleet_has_no_deadweight(db):
    """Aucun port en lourd au référentiel : le coût est déclaré non calculable."""
    await _referentials(db, dwt=None)
    _d, _n, _o, cost = await compute_route_economics(
        db, pol_locode="FRFEC", pod_locode="BRSSO", rate_unit=RATE_UNIT_TONNE
    )
    assert cost is None


# ─────────────────── Le logiciel propose, l'opérateur confirme ───────────────


@pytest.mark.asyncio
async def test_route_creation_without_price_proposes_one(db):
    from app.routers.commercial_router import grid_route_create

    await _referentials(db)
    grid = await _grid(db)
    await grid_route_create(
        grid.id,
        FakeRequest(),
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=None,
        base_rate=None,
        db=db,
        user=SimpleNamespace(id=1, full_name="A", username="a", role="administrateur"),
    )
    await db.refresh(grid, ["lines"])
    route = grid.lines[0]
    assert route.is_manual is False, "un prix non saisi reste une proposition"
    assert route.cost_rate is not None
    assert route.base_rate == suggested_price(route.cost_rate)
    assert route.margin_pct == Decimal("25.0")


@pytest.mark.asyncio
async def test_announced_price_is_kept_and_marked_confirmed(db):
    from app.routers.commercial_router import grid_route_create

    await _referentials(db)
    grid = await _grid(db)
    await grid_route_create(
        grid.id,
        FakeRequest(),
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=None,
        base_rate="850.00",
        rate_unit=RATE_UNIT_TONNE,
        db=db,
        user=SimpleNamespace(id=1, full_name="A", username="a", role="administrateur"),
    )
    await db.refresh(grid, ["lines"])
    route = grid.lines[0]
    assert route.base_rate == Decimal("850.00")
    assert route.is_manual is True
    assert route.rate_unit == RATE_UNIT_TONNE
    assert route.rate_unit_label == "€/tonne"
    # Le coût est calculé dans la même unité — sinon la marge comparerait des
    # euros par tonne à des euros par palette.
    assert route.cost_rate == route_cost_per_tonne(_OPEX, route.nav_days, Decimal("1100"))


@pytest.mark.asyncio
async def test_recalculation_refreshes_cost_and_spares_a_confirmed_price(db):
    """Le geste central de l'inversion : recalculer un coût ne bouge pas un prix."""
    from app.routers.commercial_router import grid_recalculate

    await _referentials(db)
    grid = await _grid(db)
    confirmed = RateGridLine(
        grid_id=grid.id,
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=Decimal("4500"),
        nav_days=Decimal("23.4"),
        opex_daily=Decimal("1"),
        base_rate=Decimal("999.00"),
        cost_rate=None,
        is_manual=True,
    )
    proposed = RateGridLine(
        grid_id=grid.id,
        pol_locode="BRSSO",
        pod_locode="FRFEC",
        distance_nm=Decimal("4500"),
        nav_days=Decimal("23.4"),
        opex_daily=Decimal("1"),
        base_rate=Decimal("1.00"),
        cost_rate=None,
        is_manual=False,
    )
    db.add_all([confirmed, proposed])
    await db.flush()

    await grid_recalculate(
        grid.id,
        FakeRequest(),
        db=db,
        user=SimpleNamespace(id=1, full_name="A", username="a", role="administrateur"),
    )
    await db.refresh(confirmed)
    await db.refresh(proposed)

    # Les DEUX routes voient leur coût rafraîchi — l'ancienne version sautait
    # les routes manuelles, dont la marge restait donc fausse.
    assert confirmed.cost_rate is not None
    assert proposed.cost_rate is not None
    assert confirmed.base_rate == Decimal("999.00"), "un prix confirmé est un engagement"
    assert proposed.base_rate == suggested_price(proposed.cost_rate)


@pytest.mark.asyncio
async def test_confirm_price_route_freezes_the_proposal(db):
    from app.routers.commercial_router import grid_route_confirm_price

    await _referentials(db)
    grid = await _grid(db)
    route = RateGridLine(
        grid_id=grid.id,
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=Decimal("4500"),
        nav_days=Decimal("23.4"),
        opex_daily=_OPEX,
        base_rate=Decimal("400.00"),
        cost_rate=Decimal("300.00"),
        is_manual=False,
    )
    db.add(route)
    await db.flush()

    await grid_route_confirm_price(
        grid.id,
        route.id,
        FakeRequest(),
        db=db,
        user=SimpleNamespace(id=1, full_name="A", username="a", role="administrateur"),
    )
    await db.refresh(route)
    assert route.is_manual is True
    assert route.base_rate == Decimal("400.00"), "confirmer ne modifie pas le prix"


# ───────────────────────── Cotation à la tonne ─────────────────────────


def _grid_for_quote(unit: str, rate: str) -> tuple[RateGrid, RateGridLine]:
    grid = RateGrid(
        id=1,
        reference="RG-2026-0001",
        status="active",
        valid_from=date(2026, 1, 1),
        adjustment_index=Decimal("1.0"),
        currency="EUR",
        brackets_json='[{"key": "flat", "label": "Tarif unique", "max_qty": null, "coeff": 1.0}]',
    )
    grid.options = []
    route = RateGridLine(
        grid_id=1,
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=Decimal("4500"),
        nav_days=Decimal("23.4"),
        opex_daily=_OPEX,
        base_rate=Decimal(rate),
        cost_rate=Decimal("200.00"),
        rate_unit=unit,
    )
    return grid, route


def test_quote_at_tonne_prices_the_declared_tonnage():
    grid, route = _grid_for_quote(RATE_UNIT_TONNE, "150.00")
    quote = compute_grid_quote(grid, route, items=[("EPAL", 20)], tonnage_t=Decimal("18.5"))
    assert quote.rate_unit == RATE_UNIT_TONNE
    assert quote.rate_unit_short == "tonne"
    freight = [line for line in quote.lines if line.kind == "freight"]
    assert len(freight) == 1
    assert freight[0].unit == "per_tonne"
    assert freight[0].quantity == Decimal("18.5")
    assert freight[0].total_eur == Decimal("2775.00")  # 150 × 18,5


def test_quote_at_tonne_refuses_to_guess_a_tonnage():
    """Sans tonnage déclaré, refuser est la seule réponse honnête."""
    grid, route = _grid_for_quote(RATE_UNIT_TONNE, "150.00")
    with pytest.raises(QuotingError, match="tonne"):
        compute_grid_quote(grid, route, items=[("EPAL", 20)])


def test_quote_at_palette_is_unchanged():
    """Non-régression : le rail historique n'est pas touché."""
    grid, route = _grid_for_quote(RATE_UNIT_PALETTE, "300.00")
    quote = compute_grid_quote(grid, route, items=[("EPAL", 10)])
    assert quote.rate_unit == RATE_UNIT_PALETTE
    assert quote.rate_unit_short == "palette"
    assert quote.freight_subtotal_eur == Decimal("3000.00")


# ───────────────── COM-13 — une commande naît d'un engagement ────────────────


def test_there_is_no_free_form_order_creation_route():
    from app.routers.commercial_router import router

    methods = {(m, r.path) for r in router.routes for m in (getattr(r, "methods", None) or [])}
    assert ("POST", "/commercial/orders") not in methods
    assert ("GET", "/commercial/orders/new") in methods
    assert ("POST", "/commercial/offers/{offer_id}/convert") in methods


# ─────────────── Suppression d'une pièce émise : administrateur ──────────────


@pytest.mark.asyncio
async def test_grid_deletion_is_refused_to_a_non_administrator(db):
    from app.routers.commercial_router import grid_delete

    grid = await _grid(db)
    with pytest.raises(HTTPException) as exc:
        await grid_delete(grid.id, FakeRequest(), db=db, user=_non_admin())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_grid_deletion_names_what_blocks_it(db):
    from app.routers.commercial_router import grid_delete

    admin = SimpleNamespace(id=1, full_name="A", username="a", role="administrateur")
    client = Client(id=1, name="Café du Port", client_type="shipper")
    db.add(client)
    grid = await _grid(db, client_id=1)
    db.add(
        RateOffer(
            reference="RO-2026-0001",
            client_id=1,
            grid_id=grid.id,
            title="Campagne café",
            status="en_cours",
        )
    )
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await grid_delete(grid.id, FakeRequest(), db=db, user=admin)
    assert exc.value.status_code == 400
    assert "offre" in exc.value.detail


@pytest.mark.asyncio
async def test_grid_deletion_is_blocked_by_an_order_citing_only_its_route(db):
    """Une commande peut ne citer que la **ligne-route**, pas l'en-tête.

    Les lignes tombent en cascade avec la grille : ne contrôler que
    ``Order.rate_grid_id`` faisait sortir la suppression en erreur d'intégrité
    au lieu du refus nommé que la route promet.
    """
    from app.routers.commercial_router import grid_delete

    admin = SimpleNamespace(id=1, full_name="A", username="a", role="administrateur")
    db.add(Client(id=1, name="Café du Port", client_type="shipper"))
    grid = await _grid(db, client_id=1)
    route = RateGridLine(
        grid_id=grid.id,
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        distance_nm=Decimal("4500"),
        nav_days=Decimal("23.4"),
        opex_daily=_OPEX,
        base_rate=Decimal("400.00"),
        cost_rate=Decimal("300.00"),
    )
    db.add(route)
    await db.flush()
    db.add(
        Order(
            reference="ORD-2026-0002",
            client_id=1,
            status="confirmed",
            rate_grid_id=None,  # seule la ligne est citée
            rate_grid_line_id=route.id,
        )
    )
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await grid_delete(grid.id, FakeRequest(), db=db, user=admin)
    assert exc.value.status_code == 400
    assert "commande" in exc.value.detail


@pytest.mark.asyncio
async def test_offer_correction_refuses_another_clients_grid(db):
    """Règle d'or : une grille négociée ne se sert qu'à son client.

    L'écran ne propose que les grilles servables — le POST doit le revalider,
    sinon un formulaire rejoué attacherait la grille d'un tiers à l'offre.
    """
    from datetime import date as _d

    from app.routers.commercial_router import offer_edit

    admin = SimpleNamespace(id=1, full_name="A", username="a", role="administrateur")
    db.add_all(
        [
            Client(id=1, name="Café du Port", client_type="shipper"),
            Client(id=2, name="Concurrent SA", client_type="shipper"),
        ]
    )
    await db.flush()
    foreign = RateGrid(
        id=99,
        reference="RG-2026-0099",
        client_id=2,
        status="active",
        valid_from=_d(2026, 1, 1),
    )
    db.add(foreign)
    db.add(
        RateOffer(id=1, reference="RO-2026-0004", client_id=1, title="Campagne", status="en_cours")
    )
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await offer_edit(
            1,
            FakeRequest(),
            title="Campagne",
            grid_id="99",
            db=db,
            user=admin,
        )
    assert exc.value.status_code == 400
    assert "client" in exc.value.detail


@pytest.mark.asyncio
async def test_offer_deletion_is_blocked_by_the_order_it_produced(db):
    from app.routers.commercial_router import offer_delete

    admin = SimpleNamespace(id=1, full_name="A", username="a", role="administrateur")
    db.add(Client(id=1, name="Café du Port", client_type="shipper"))
    offer = RateOffer(
        id=1, reference="RO-2026-0001", client_id=1, title="Campagne café", status="valide"
    )
    db.add(offer)
    await db.flush()
    db.add(Order(reference="ORD-2026-0001", client_id=1, offer_id=1, status="confirmed"))
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await offer_delete(1, FakeRequest(), db=db, user=admin)
    assert exc.value.status_code == 400
    assert "ORD-2026-0001" in exc.value.detail


@pytest.mark.asyncio
async def test_offer_without_downstream_artefact_can_be_deleted(db):
    from app.routers.commercial_router import offer_delete

    admin = SimpleNamespace(id=1, full_name="A", username="a", role="administrateur")
    db.add(Client(id=1, name="Café du Port", client_type="shipper"))
    db.add(RateOffer(id=1, reference="RO-2026-0002", client_id=1, title="Essai", status="annule"))
    await db.flush()

    await offer_delete(1, FakeRequest(), db=db, user=admin)
    assert await db.get(RateOffer, 1) is None


@pytest.mark.asyncio
async def test_estimation_deletion_is_blocked_once_converted(db):
    from app.routers.commercial_router import devis_delete

    admin = SimpleNamespace(id=1, full_name="A", username="a", role="administrateur")
    db.add(Client(id=1, name="Café du Port", client_type="shipper"))
    db.add(
        RateOffer(
            id=1, reference="RO-2026-0003", client_id=1, title="Issue devis", status="en_cours"
        )
    )
    await db.flush()
    db.add(
        Quote(
            reference="DEV-2026-ABCDEF",
            pol_locode="FRFEC",
            pod_locode="BRSSO",
            palettes_total=10,
            converted_offer_id=1,
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await devis_delete("DEV-2026-ABCDEF", FakeRequest(), db=db, user=admin)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_client_creation_is_reserved_to_the_administrator(db):
    """La base client vient de Pipedrive ; une saisie parallèle fait un doublon."""
    from app.routers.commercial_router import client_create

    with pytest.raises(HTTPException) as exc:
        await client_create(
            FakeRequest(),
            name="Doublon SARL",
            client_type="shipper",
            db=db,
            user=_non_admin(),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_strategic_client_flag_is_the_only_anchor_attribute(db):
    from app.routers.commercial_router import client_anchor_update

    admin = SimpleNamespace(id=1, full_name="A", username="a", role="administrateur")
    client = Client(id=1, name="Café du Port", client_type="shipper")
    db.add(client)
    await db.flush()

    await client_anchor_update(1, FakeRequest(), is_anchor=True, db=db, user=admin)
    await db.refresh(client)
    assert client.is_anchor is True
    # Les trois attributs qui n'étaient consommés par aucune règle ne sont plus
    # écrits : ils restent à leur valeur par défaut.
    assert client.annual_volume_commitment is None
    assert client.capacity_priority == 0
