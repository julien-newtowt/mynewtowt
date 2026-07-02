"""Grilles tarifaires multi-routes (P11) — tests d'intégration.

Couvre le cœur tarifaire Module 6 : résolution de la grille client par
**route** (POL/POD), calcul du devis via le ``base_rate`` de la ligne-route +
brackets de la grille, et recalcul OPEX **par route**.

Critère d'acceptation (PLAN_GRILLES_MULTIROUTES.md) :
- grille Shipper « Client X · 2026 » avec 2 routes (FRFEC→BRSSO, BRSSO→FRFEC),
  chacune sa distance → base_rate OPEX distinct ;
- devis 200 palettes sur la route 1 = base_rate(route1) × 0.80 × adjustment_index.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.models.commercial import (
    DEFAULT_BRACKETS_SHIPPER,
    Client,
    RateGrid,
    RateGridLine,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.commercial import next_grid_reference
from app.services.quoting import (
    compute_grid_quote,
    compute_route_economics,
    resolve_grid,
    route_base_rate,
    route_nav_days,
)

# OPEX/j du navire de référence : pilote le base_rate de chaque route.
_OPEX_DAILY = Decimal("12000")
_ROUTE1 = ("FRFEC", "BRSSO", Decimal("4500"))  # aller
_ROUTE2 = ("BRSSO", "FRFEC", Decimal("5200"))  # retour (routage distinct → base distinct)


async def _setup_client_grid(db, *, status: str = "active", adjustment: str = "1.0500"):
    """Crée « Client X » + une grille 2026 multi-routes (2 routes) sur navire OPEX."""
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    db.add(Vessel(id=1, code="ANE", name="Anemos", opex_daily_sea_eur=float(_OPEX_DAILY)))
    client = Client(name="Client X", client_type="shipper")
    db.add(client)
    await db.flush()

    grid = RateGrid(
        reference=await next_grid_reference(db, year=2026),
        client_id=client.id,
        vessel_id=1,
        is_default=False,
        status=status,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        currency="EUR",
        adjustment_index=Decimal(adjustment),
        brackets_json=json.dumps(DEFAULT_BRACKETS_SHIPPER),
    )
    db.add(grid)
    await db.flush()

    for pol, pod, distance in (_ROUTE1, _ROUTE2):
        dist, nav_days, opex, base = await compute_route_economics(
            db, pol_locode=pol, pod_locode=pod, vessel_id=1, distance_nm=distance
        )
        db.add(
            RateGridLine(
                grid_id=grid.id,
                pol_locode=pol,
                pod_locode=pod,
                distance_nm=dist,
                nav_days=nav_days,
                opex_daily=opex,
                base_rate=base,
                is_manual=False,
            )
        )
    await db.flush()
    await db.refresh(grid, attribute_names=["lines"])
    return grid, client


# ─────────────────────────── resolve_grid multi-routes ──────────────────────


@pytest.mark.asyncio
async def test_resolve_grid_matches_client_route_per_direction(db):
    """La grille client est résolue par la ligne-route POL/POD, base_rate distinct."""
    grid, client = await _setup_client_grid(db)

    g1, r1 = await resolve_grid(
        db,
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        on_date=date(2026, 6, 1),
        commercial_client_id=client.id,
    )
    g2, r2 = await resolve_grid(
        db,
        pol_locode="BRSSO",
        pod_locode="FRFEC",
        on_date=date(2026, 6, 1),
        commercial_client_id=client.id,
    )
    # Même grille (client), routes distinctes.
    assert g1.id == grid.id and g2.id == grid.id
    assert not g1.is_default
    assert (r1.pol_locode, r1.pod_locode) == ("FRFEC", "BRSSO")
    assert (r2.pol_locode, r2.pod_locode) == ("BRSSO", "FRFEC")
    # base_rate OPEX distinct par route (distances différentes).
    assert r1.base_rate != r2.base_rate
    assert r1.base_rate == route_base_rate(_OPEX_DAILY, route_nav_days(_ROUTE1[2]))
    assert r2.base_rate == route_base_rate(_OPEX_DAILY, route_nav_days(_ROUTE2[2]))


@pytest.mark.asyncio
async def test_resolve_grid_falls_back_to_default_when_route_absent(db):
    """Route non couverte par la grille client → repli grille par défaut (créée)."""
    _grid, client = await _setup_client_grid(db)
    db.add(Port(id=3, locode="USNYC", name="New York", country="US"))
    await db.flush()
    grid, route = await resolve_grid(
        db,
        pol_locode="FRFEC",
        pod_locode="USNYC",
        on_date=date(2026, 6, 1),
        commercial_client_id=client.id,
    )
    assert grid.is_default is True
    assert grid.client_id is None
    assert (route.pol_locode, route.pod_locode) == ("FRFEC", "USNYC")


# ─────────────────── compute_grid_quote par route (acceptance) ───────────────


@pytest.mark.asyncio
async def test_quote_200_palettes_route1_is_base_x_080_x_adjustment(db):
    """Acceptance : devis 200 palettes route1 = base_rate(route1) × 0.80 × index."""
    grid, client = await _setup_client_grid(db, adjustment="1.0500")
    _g, route1 = await resolve_grid(
        db,
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        on_date=date(2026, 6, 1),
        commercial_client_id=client.id,
    )
    quote = compute_grid_quote(grid, route1, items=[("EPAL", 200)])

    expected = (route1.base_rate * grid.adjustment_index * Decimal("0.80")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    # bracket 200 palettes → coeff 0.80 (DEFAULT_BRACKETS_SHIPPER).
    assert quote.bracket_label == "200 palettes"
    assert quote.base_rate_eur == expected
    epal = next(li for li in quote.lines if li.kind == "freight")
    assert epal.unit_price_eur == expected
    assert epal.total_eur == (expected * 200).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@pytest.mark.asyncio
async def test_quote_distinct_per_route_same_grid(db):
    """La même grille cote chaque route via le base_rate de sa ligne."""
    grid, client = await _setup_client_grid(db, adjustment="1.0000")
    _g1, r1 = await resolve_grid(
        db,
        pol_locode="FRFEC",
        pod_locode="BRSSO",
        on_date=date(2026, 6, 1),
        commercial_client_id=client.id,
    )
    _g2, r2 = await resolve_grid(
        db,
        pol_locode="BRSSO",
        pod_locode="FRFEC",
        on_date=date(2026, 6, 1),
        commercial_client_id=client.id,
    )
    q1 = compute_grid_quote(grid, r1, items=[("EPAL", 200)])
    q2 = compute_grid_quote(grid, r2, items=[("EPAL", 200)])
    assert q1.base_rate_eur != q2.base_rate_eur


# ─────────────────────────── recalcul OPEX par route ─────────────────────────


@pytest.mark.asyncio
async def test_global_recalc_recomputes_each_route_base_rate(db, staff_user):
    """Le recalcul global réécrit le base_rate OPEX (navire) de chaque route.

    NB : le recalcul re-résout la distance de chaque route (leg/ports) — on
    vérifie donc que la **formule OPEX** est appliquée par route avec l'OPEX du
    navire de la grille, pas une distance figée.
    """
    from app.routers.commercial_router import grid_recalculate
    from tests.integration.conftest import FakeRequest

    grid, _client = await _setup_client_grid(db, status="draft")
    # On casse les base_rate pour vérifier qu'ils sont bien recalculés.
    for line in grid.lines:
        line.base_rate = Decimal("1.00")
    await db.flush()

    resp = await grid_recalculate(grid.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    await db.refresh(grid, attribute_names=["lines"])
    assert len(grid.lines) == 2
    for li in grid.lines:
        # OPEX/j repris du navire de la grille, base_rate = OPEX × jours / 978.
        assert li.opex_daily == _OPEX_DAILY
        assert li.base_rate == route_base_rate(_OPEX_DAILY, li.nav_days)
        assert li.base_rate != Decimal("1.00")  # bien recalculé


@pytest.mark.asyncio
async def test_global_recalc_skips_manual_routes(db, staff_user):
    """Une route à base_rate manuel n'est pas réécrite par le recalcul OPEX."""
    from app.routers.commercial_router import grid_recalculate
    from tests.integration.conftest import FakeRequest

    grid, _client = await _setup_client_grid(db, status="draft")
    manual = grid.lines[0]
    manual.is_manual = True
    manual.base_rate = Decimal("999.99")
    await db.flush()

    await grid_recalculate(grid.id, FakeRequest(), db=db, user=staff_user)
    await db.refresh(grid, attribute_names=["lines"])
    kept = next(li for li in grid.lines if li.id == manual.id)
    assert kept.base_rate == Decimal("999.99")  # figé (surcharge manuelle)
    assert kept.is_manual is True


@pytest.mark.asyncio
async def test_route_level_recalc_clears_manual_override(db, staff_user):
    """Le recalcul d'une route efface la surcharge manuelle (retour OPEX)."""
    from app.routers.commercial_router import grid_route_recalculate
    from tests.integration.conftest import FakeRequest

    grid, _client = await _setup_client_grid(db, status="draft")
    route = grid.lines[0]
    route.is_manual = True
    route.base_rate = Decimal("999.99")
    await db.flush()

    await grid_route_recalculate(grid.id, route.id, FakeRequest(), db=db, user=staff_user)
    await db.refresh(route)
    assert route.is_manual is False
    assert route.opex_daily == _OPEX_DAILY
    assert route.base_rate == route_base_rate(_OPEX_DAILY, route.nav_days)
    assert route.base_rate != Decimal("999.99")  # surcharge manuelle effacée
