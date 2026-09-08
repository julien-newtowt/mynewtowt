"""Page « nouvelle offre » — le champ vide, le filtre par leg, et le prix.

Trois défauts constatés le 2026-09-07, du plus visible au plus grave :

1. choisir un client affichait « Action refusée — rechargez la page », et la
   liste des grilles ne se filtrait jamais (``hx-include`` envoie ``leg_id=``,
   que ``int | None`` refuse → 422 avant la route) ;
2. l'écran annonçait « filtrée par client + leg » — le ``leg_id`` était bien
   transmis, puis **ignoré** ;
3. une grille ne couvrant pas la route du voyage faisait coter l'offre sur
   ``grid.lines[0]``, la **première route de la grille**. Un Fécamp→Santos
   pouvait ainsi porter le tarif d'un Le Havre→Fort-de-France, silencieusement,
   et c'est ce prix qui part sur la booking note.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.commercial import Client, RateGrid, RateGridLine
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel

from .conftest import FakeRequest

D0 = datetime(2026, 5, 1, tzinfo=UTC)
_ADMIN = SimpleNamespace(id=1, full_name="Admin", username="admin", role="administrateur")


async def _referentials(db):
    db.add_all(
        [
            Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"),
            Port(id=1, locode="FRFEC", name="Fécamp", country="FR"),
            Port(id=2, locode="BRSSO", name="São Sebastião", country="BR"),
            Port(id=3, locode="FRLEH", name="Le Havre", country="FR"),
            Port(id=4, locode="MQFDF", name="Fort-de-France", country="MQ"),
            Client(id=10, name="Café du Port", client_type="shipper"),
        ]
    )
    await db.flush()


async def _leg(db, *, leg_id=1, code="1AFRBR6", pol=1, pod=2):
    leg = Leg(
        id=leg_id,
        leg_code=code,
        vessel_id=1,
        departure_port_id=pol,
        arrival_port_id=pod,
        etd_ref=D0,
        eta_ref=D0 + timedelta(days=25),
        etd=D0,
        eta=D0 + timedelta(days=25),
    )
    db.add(leg)
    await db.flush()
    return leg


async def _grid(db, *, grid_id, reference, client_id, routes, is_default=False):
    grid = RateGrid(
        id=grid_id,
        reference=reference,
        client_id=client_id,
        is_default=is_default,
        status="active",
        valid_from=date(2026, 1, 1),
        adjustment_index=Decimal("1.0"),
        brackets_json='[{"key": "flat", "label": "Tarif unique", "max_qty": null, "coeff": 1.0}]',
    )
    db.add(grid)
    await db.flush()
    for pol, pod, rate in routes:
        db.add(
            RateGridLine(
                grid_id=grid.id,
                pol_locode=pol,
                pod_locode=pod,
                distance_nm=Decimal("4500"),
                nav_days=Decimal("23.4"),
                opex_daily=Decimal("12000"),
                base_rate=Decimal(rate),
                cost_rate=Decimal("200.00"),
            )
        )
    await db.flush()
    return grid


# ────────── 1. Le champ vide ne doit plus produire un refus ──────────


@pytest.mark.asyncio
async def test_grid_options_accepts_a_blank_leg_id(db):
    """``hx-include`` envoie ``leg_id=`` tant que le voyage n'est pas choisi.

    C'est le défaut signalé : chaque changement de client répondait 422, et
    ``toast.js`` en tirait « Action refusée — rechargez la page ».
    """
    from app.routers.commercial_router import offer_grid_options

    await _referentials(db)
    await _grid(
        db, grid_id=1, reference="RG-2026-0001", client_id=10, routes=[("FRFEC", "BRSSO", "400")]
    )

    resp = await offer_grid_options(FakeRequest(), client_id=10, leg_id=None, db=db, user=_ADMIN)
    assert resp.status_code == 200
    assert "RG-2026-0001" in resp.body.decode()


def test_blank_query_value_is_read_as_absent():
    """Le contrat de l'alias, au niveau de la validation elle-même."""
    from pydantic import TypeAdapter

    from app.utils.query import OptionalInt

    assert TypeAdapter(OptionalInt).validate_python("") is None


# ────────── 2. « filtrée par client + leg » doit être vrai ──────────


@pytest.mark.asyncio
async def test_a_client_grid_not_covering_the_leg_route_is_marked(db):
    """La grille reste listée mais **désignée** : la masquer sans dire pourquoi
    se lirait comme une panne."""
    from app.routers.commercial_router import _grids_for

    await _referentials(db)
    leg = await _leg(db)  # FRFEC → BRSSO
    await _grid(
        db, grid_id=1, reference="RG-COUVRE", client_id=10, routes=[("FRFEC", "BRSSO", "400")]
    )
    await _grid(
        db, grid_id=2, reference="RG-AILLEURS", client_id=10, routes=[("FRLEH", "MQFDF", "300")]
    )

    grids = {g.reference: g for g in await _grids_for(db, client_id=10, leg_id=leg.id)}
    assert grids["RG-COUVRE"].covers_leg is True
    assert grids["RG-AILLEURS"].covers_leg is False


@pytest.mark.asyncio
async def test_covering_grids_are_listed_first(db):
    from app.routers.commercial_router import _grids_for

    await _referentials(db)
    leg = await _leg(db)
    await _grid(
        db, grid_id=1, reference="RG-AAA-AILLEURS", client_id=10, routes=[("FRLEH", "MQFDF", "300")]
    )
    await _grid(
        db, grid_id=2, reference="RG-ZZZ-COUVRE", client_id=10, routes=[("FRFEC", "BRSSO", "400")]
    )

    ordered = await _grids_for(db, client_id=10, leg_id=leg.id)
    assert next(g.reference for g in ordered) == "RG-ZZZ-COUVRE"


@pytest.mark.asyncio
async def test_a_default_grid_is_always_eligible(db):
    """``resolve_grid`` y matérialise la route à la demande (*get-or-create*) :
    l'absence de ligne n'y signifie pas l'absence de couverture."""
    from app.routers.commercial_router import _grids_for

    await _referentials(db)
    leg = await _leg(db)
    await _grid(db, grid_id=1, reference="RGD-2026", client_id=None, routes=[], is_default=True)

    grids = await _grids_for(db, client_id=10, leg_id=leg.id)
    assert [g.covers_leg for g in grids] == [True]


@pytest.mark.asyncio
async def test_without_a_leg_every_grid_is_eligible(db):
    """Sans voyage choisi, il n'y a rien à filtrer — et surtout rien à exclure."""
    from app.routers.commercial_router import _grids_for

    await _referentials(db)
    await _grid(
        db, grid_id=1, reference="RG-AILLEURS", client_id=10, routes=[("FRLEH", "MQFDF", "300")]
    )

    grids = await _grids_for(db, client_id=10, leg_id=None)
    assert all(g.covers_leg for g in grids)


# ────────── 3. Aucun prix pris sur une route arbitraire ──────────


@pytest.mark.asyncio
async def test_offer_creation_refuses_a_grid_that_does_not_cover_the_route(db):
    """Le défaut le plus grave : l'offre était cotée sur ``grid.lines[0]``.

    Un tarif qu'aucune route ne justifie n'est pas un repli, c'est une erreur
    silencieuse — et c'est ce prix qui part sur la booking note.
    """
    from app.routers.commercial_router import offer_create

    await _referentials(db)
    leg = await _leg(db)  # FRFEC → BRSSO
    # La grille ne porte que Le Havre → Fort-de-France, à un tarif très différent.
    await _grid(
        db, grid_id=1, reference="RG-AILLEURS", client_id=10, routes=[("FRLEH", "MQFDF", "999")]
    )

    with pytest.raises(HTTPException) as exc:
        await offer_create(
            FakeRequest(),
            client_id=10,
            grid_id=1,
            leg_id=leg.id,
            title="Campagne café",
            estimated_palettes=40,
            db=db,
            user=_ADMIN,
        )
    assert exc.value.status_code == 400
    assert "FRFEC→BRSSO" in exc.value.detail
    assert "RG-AILLEURS" in exc.value.detail


@pytest.mark.asyncio
async def test_offer_creation_prices_on_the_leg_route(db):
    """Cas nominal : c'est bien la ligne du voyage qui donne le tarif."""
    from sqlalchemy import select

    from app.models.commercial import RateOffer
    from app.routers.commercial_router import offer_create

    await _referentials(db)
    leg = await _leg(db)
    await _grid(
        db,
        grid_id=1,
        reference="RG-2026-0001",
        client_id=10,
        # La route du leg n'est PAS la première ligne : c'est ce qui distingue
        # « coté sur la bonne route » de « coté sur la première ».
        routes=[("FRLEH", "MQFDF", "999"), ("FRFEC", "BRSSO", "400")],
    )

    resp = await offer_create(
        FakeRequest(),
        client_id=10,
        grid_id=1,
        leg_id=leg.id,
        title="Campagne café",
        estimated_palettes=40,
        db=db,
        user=_ADMIN,
    )
    assert resp.status_code == 303
    offer = (await db.execute(select(RateOffer))).scalars().one()
    assert offer.proposed_rate_eur == Decimal("400.00")
    assert offer.total_eur == Decimal("16000.00")
