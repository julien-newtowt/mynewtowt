"""Grilles tarifaires — référence codifiée, échéances, défaut par route (lot 2).

Couvre les règles métier arbitrées le 2026-08-26 :

* référence de route ``P-[MMAA]-[MMAA]-[XX]-[YY]`` (pays en ISO alpha-2) ;
* un client peut avoir **plusieurs grilles actives** simultanément, celle marquée
  par défaut sur la route l'emportant à la résolution ;
* conditions de règlement : 1 à 3 échéances totalisant 100 %, déclaratives ;
* tarif de base calculé sur la **capacité et la vitesse réelles** quand elles
  sont connues, et non sur les constantes historiques.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.commercial import Client, RateGrid, RateGridLine
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.commercial import (
    PaymentTermError,
    assign_tariff_reference,
    route_tariff_reference,
    validate_payment_terms,
)
from app.services.quoting import resolve_grid, route_base_rate, route_nav_days

# ─────────────────────────── Référence codifiée ───────────────────────────


def test_route_reference_uses_iso2_country_codes():
    ref = route_tariff_reference(
        valid_from=date(2026, 3, 1),
        valid_to=date(2026, 9, 30),
        pol_country="FR",
        pod_country="BR",
    )
    assert ref == "P-0326-0926-FR-BR"


def test_route_reference_marks_open_validity_and_unknown_country():
    """Une validité ouverte ne s'invente pas une échéance, un pays inconnu se voit."""
    assert route_tariff_reference(
        valid_from=date(2026, 12, 1), valid_to=None, pol_country="FR", pod_country=None
    ) == "P-1226------FR-??"


@pytest.mark.asyncio
async def test_assign_tariff_reference_reads_country_from_ports(db):
    db.add_all(
        [
            Port(id=1, locode="FRLEH", name="Le Havre", country="FR"),
            Port(id=2, locode="BRSSZ", name="Santos", country="BR"),
        ]
    )
    client = Client(name="Café du Port", client_type="shipper")
    db.add(client)
    await db.flush()

    grid = RateGrid(
        reference="RG-2026-0001",
        client_id=client.id,
        status="draft",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 6, 30),
    )
    db.add(grid)
    await db.flush()
    route = RateGridLine(
        grid_id=grid.id,
        pol_locode="FRLEH",
        pod_locode="BRSSZ",
        distance_nm=Decimal("5000"),
        nav_days=Decimal("26.042"),
        opex_daily=Decimal("12000"),
        base_rate=Decimal("319.53"),
    )
    db.add(route)
    await db.flush()

    assert await assign_tariff_reference(db, grid, route) == "P-0126-0626-FR-BR"
    assert route.tariff_reference == "P-0126-0626-FR-BR"


# ────────────────────── Conditions de règlement ───────────────────────────


def test_payment_terms_must_total_exactly_100_percent():
    with pytest.raises(PaymentTermError, match="100"):
        validate_payment_terms(
            [
                {"trigger": "before_loading", "percentage": "40"},
                {"trigger": "before_discharge", "percentage": "40"},
            ]
        )


def test_payment_terms_accept_three_installments():
    terms = validate_payment_terms(
        [
            {"trigger": "days_before_etd", "percentage": "30", "offset_days": "30"},
            {"trigger": "before_loading", "percentage": "40"},
            {"trigger": "before_discharge", "percentage": "30", "label": "Solde"},
        ]
    )
    assert [t["position"] for t in terms] == [1, 2, 3]
    assert terms[0]["offset_days"] == 30
    assert terms[2]["label"] == "Solde"
    assert sum(t["percentage"] for t in terms) == Decimal("100.00")


def test_payment_terms_reject_more_than_three():
    with pytest.raises(PaymentTermError, match="3"):
        validate_payment_terms(
            [{"trigger": "before_loading", "percentage": "25"} for _ in range(4)]
        )


def test_days_before_etd_requires_a_number_of_days():
    with pytest.raises(PaymentTermError, match="jours"):
        validate_payment_terms([{"trigger": "days_before_etd", "percentage": "100"}])


def test_payment_terms_reject_unknown_trigger():
    with pytest.raises(PaymentTermError, match="[Dd]éclencheur"):
        validate_payment_terms([{"trigger": "a_la_livraison", "percentage": "100"}])


def test_empty_payment_terms_are_allowed():
    """Une grille sans conditions particulières reste valide."""
    assert validate_payment_terms([]) == []


# ───────────── Plusieurs grilles actives + défaut par route ───────────────


async def _grid_with_route(
    db,
    client: Client,
    *,
    reference: str,
    base_rate: str,
    valid_from: date,
    is_route_default: bool = False,
) -> RateGrid:
    grid = RateGrid(
        reference=reference,
        client_id=client.id,
        status="active",
        valid_from=valid_from,
        valid_to=date(2026, 12, 31),
    )
    db.add(grid)
    await db.flush()
    db.add(
        RateGridLine(
            grid_id=grid.id,
            pol_locode="FRLEH",
            pod_locode="BRSSZ",
            distance_nm=Decimal("5000"),
            nav_days=Decimal("26.042"),
            opex_daily=Decimal("12000"),
            base_rate=Decimal(base_rate),
            is_route_default=is_route_default,
        )
    )
    await db.flush()
    return grid


@pytest.mark.asyncio
async def test_route_default_grid_wins_over_the_most_recent(db):
    """Deux grilles actives sur la même route : celle marquée par défaut gagne."""
    db.add_all(
        [
            Port(id=1, locode="FRLEH", name="Le Havre", country="FR"),
            Port(id=2, locode="BRSSZ", name="Santos", country="BR"),
        ]
    )
    client = Client(name="Cacao Négoce", client_type="freight_forwarder")
    db.add(client)
    await db.flush()

    # La plus récente n'est PAS celle par défaut — sans le drapeau, elle gagnerait.
    await _grid_with_route(
        db, client, reference="RG-2026-0001", base_rate="300.00",
        valid_from=date(2026, 1, 1), is_route_default=True,
    )
    await _grid_with_route(
        db, client, reference="RG-2026-0002", base_rate="450.00",
        valid_from=date(2026, 5, 1),
    )

    grid, route = await resolve_grid(
        db,
        pol_locode="FRLEH",
        pod_locode="BRSSZ",
        on_date=date(2026, 6, 15),
        commercial_client_id=client.id,
    )
    assert grid.reference == "RG-2026-0001"
    assert route.base_rate == Decimal("300.00")


# ──────────── Tarif de base : capacité et vitesse réelles ─────────────────


def test_base_rate_uses_the_real_vessel_capacity():
    """Une capacité réelle plus faible renchérit la palette — et inversement."""
    reference = route_base_rate(Decimal("12000"), Decimal("10"))  # capacité 978
    smaller = route_base_rate(Decimal("12000"), Decimal("10"), 500)
    assert smaller > reference
    assert smaller == Decimal("240.00")  # 12000 × 10 / 500


def test_nav_days_uses_the_leg_speed_when_known():
    faster = route_nav_days(Decimal("4800"), Decimal("12"))
    default = route_nav_days(Decimal("4800"))
    assert faster < default
    assert faster == Decimal("16.667")  # 4800 / (12 × 24)


def test_absurd_speed_or_capacity_falls_back_instead_of_exploding():
    """Une vitesse ou une capacité nulle ne doit produire ni division par zéro ni tarif absurde."""
    assert route_nav_days(Decimal("4800"), Decimal("0")) == route_nav_days(Decimal("4800"))
    assert route_base_rate(Decimal("12000"), Decimal("10"), 0) == route_base_rate(
        Decimal("12000"), Decimal("10")
    )


@pytest.mark.asyncio
async def test_route_economics_reads_capacity_and_speed_from_leg(db):
    """Le navire du leg prime : c'est lui qui portera la marchandise."""
    from app.services.quoting import compute_route_economics

    db.add_all(
        [
            Port(id=1, locode="FRLEH", name="Le Havre", country="FR"),
            Port(id=2, locode="BRSSZ", name="Santos", country="BR"),
            Vessel(id=1, code="ANE", name="Anemos", capacity_palettes=500),
        ]
    )
    await db.flush()
    leg = Leg(
        leg_code="1AFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=datetime(2026, 6, 1, tzinfo=UTC),
        eta_ref=datetime(2026, 6, 25, tzinfo=UTC),
        etd=datetime(2026, 6, 1, tzinfo=UTC),
        eta=datetime(2026, 6, 25, tzinfo=UTC),
        transit_speed_kn=12.0,
    )
    db.add(leg)
    await db.flush()

    _dist, nav_days, _opex, base = await compute_route_economics(
        db,
        pol_locode="FRLEH",
        pod_locode="BRSSZ",
        leg=leg,
        distance_nm=Decimal("4800"),
    )

    assert nav_days == Decimal("16.667")  # vitesse 12 nds du leg, pas 8
    # Capacité 500 du navire, pas la constante 978.
    assert base == route_base_rate(Decimal("12000"), nav_days, 500)
