"""Page « nouvelle offre » — la cascade de filtrage, le champ vide, et le prix.

Trois défauts constatés les 2026-09-07 / 2026-09-08, du plus visible au plus
grave :

1. choisir un client affichait « Action refusée — rechargez la page », et la
   liste des grilles ne se filtrait jamais (``hx-include`` envoie un champ vide,
   que ``int | None`` refuse → 422 avant la route) ;
2. la cascade n'allait pas dans le sens du travail de l'opérateur. Elle doit
   descendre **client → grille → voyage** : le client borne ses grilles, la
   grille retenue borne les voyages qu'elle sait coter ;
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
            Client(id=11, name="Cacao Négoce", client_type="shipper"),
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
async def test_grid_options_accepts_a_blank_client_id(db):
    """``hx-include`` envoie le champ **quel qu'en soit le contenu**.

    C'est le défaut signalé : un champ vide répondait 422, et ``toast.js`` en
    tirait « Action refusée — rechargez la page ».
    """
    from app.routers.commercial_router import offer_grid_options

    await _referentials(db)
    await _grid(db, grid_id=1, reference="RGD-2026", client_id=None, routes=[], is_default=True)

    resp = await offer_grid_options(FakeRequest(), client_id=None, db=db, user=_ADMIN)
    assert resp.status_code == 200
    assert "RGD-2026" in resp.body.decode()


@pytest.mark.asyncio
async def test_leg_options_accepts_a_blank_grid_id(db):
    """« Sans grille » part comme ``grid_id=`` — un état de saisie normal."""
    from app.routers.commercial_router import offer_leg_options

    await _referentials(db)
    leg = await _leg(db)

    resp = await offer_leg_options(FakeRequest(), grid_id=None, db=db, user=_ADMIN)
    assert resp.status_code == 200
    assert leg.leg_code in resp.body.decode()


def test_blank_query_value_is_read_as_absent():
    """Le contrat de l'alias, au niveau de la validation elle-même."""
    from pydantic import TypeAdapter

    from app.utils.query import OptionalInt

    assert TypeAdapter(OptionalInt).validate_python("") is None


# ────────── 2. Maillon client → grille ──────────


@pytest.mark.asyncio
async def test_only_the_clients_grids_are_listed(db):
    """La grille négociée d'un autre client n'a rien à faire dans la liste.

    Ce n'est pas de l'ergonomie : la servir ferait coter un client au tarif
    d'un autre.
    """
    from app.routers.commercial_router import _grids_for

    await _referentials(db)
    await _grid(
        db, grid_id=1, reference="RG-CAFE", client_id=10, routes=[("FRFEC", "BRSSO", "400")]
    )
    await _grid(
        db, grid_id=2, reference="RG-CACAO", client_id=11, routes=[("FRLEH", "MQFDF", "300")]
    )
    await _grid(db, grid_id=3, reference="RGD-2026", client_id=None, routes=[], is_default=True)

    references = [g.reference for g in await _grids_for(db, client_id=10)]
    assert "RG-CACAO" not in references
    # La grille par défaut reste proposée : c'est le repli d'un client sans
    # grille négociée, et l'étiquette de l'option le dit.
    assert references == ["RG-CAFE", "RGD-2026"]


@pytest.mark.asyncio
async def test_without_a_client_only_default_grids_are_listed(db):
    from app.routers.commercial_router import _grids_for

    await _referentials(db)
    await _grid(
        db, grid_id=1, reference="RG-CAFE", client_id=10, routes=[("FRFEC", "BRSSO", "400")]
    )
    await _grid(db, grid_id=3, reference="RGD-2026", client_id=None, routes=[], is_default=True)

    assert [g.reference for g in await _grids_for(db, client_id=None)] == ["RGD-2026"]


@pytest.mark.asyncio
async def test_grid_options_chains_to_the_leg_list_after_the_swap(db):
    """Le client a changé : la grille retenue a disparu, donc la liste des
    voyages n'est plus à jour. L'en-tête chaîne le second fragment.

    ``HX-Trigger-After-Swap`` et non ``HX-Trigger`` : avant le remplacement des
    options, le ``<select>`` grille porte encore la grille du client précédent,
    et les voyages seraient bornés par une grille qui vient de quitter l'écran.
    """
    import json

    from app.routers.commercial_router import offer_grid_options

    await _referentials(db)
    resp = await offer_grid_options(FakeRequest(), client_id=10, db=db, user=_ADMIN)
    assert "HX-Trigger" not in resp.headers
    assert json.loads(resp.headers["HX-Trigger-After-Swap"]) == {"grid-options-loaded": True}


# ────────── 3. Maillon grille → voyage ──────────


@pytest.mark.asyncio
async def test_a_client_grid_bounds_the_legs_to_its_own_routes(db):
    """Le voyage proposé doit être cotable par la grille choisie."""
    from app.routers.commercial_router import offer_leg_options

    await _referentials(db)
    covered = await _leg(db, leg_id=1, code="1AFRBR6", pol=1, pod=2)  # FRFEC → BRSSO
    other = await _leg(db, leg_id=2, code="1BFRMQ6", pol=3, pod=4)  # FRLEH → MQFDF
    await _grid(
        db, grid_id=1, reference="RG-CAFE", client_id=10, routes=[("FRFEC", "BRSSO", "400")]
    )

    body = (await offer_leg_options(FakeRequest(), grid_id=1, db=db, user=_ADMIN)).body.decode()
    assert covered.leg_code in body
    assert other.leg_code not in body


@pytest.mark.asyncio
async def test_a_client_grid_without_route_says_why_the_list_is_empty(db):
    """Aucune route ⇒ aucun voyage cotable. Un menu vide sans explication se
    lirait comme une panne, alors que c'est un fait métier."""
    from app.routers.commercial_router import offer_leg_options

    await _referentials(db)
    await _leg(db)
    await _grid(db, grid_id=1, reference="RG-VIDE", client_id=10, routes=[])

    body = (await offer_leg_options(FakeRequest(), grid_id=1, db=db, user=_ADMIN)).body.decode()
    assert "1AFRBR6" not in body
    assert "RG-VIDE" in body


@pytest.mark.asyncio
async def test_a_default_grid_bounds_nothing(db):
    """``resolve_grid`` y matérialise la route à la demande (*get-or-create*) :
    l'absence de ligne n'y signifie pas l'absence de couverture."""
    from app.routers.commercial_router import offer_leg_options

    await _referentials(db)
    leg = await _leg(db)
    await _grid(db, grid_id=1, reference="RGD-2026", client_id=None, routes=[], is_default=True)

    body = (await offer_leg_options(FakeRequest(), grid_id=1, db=db, user=_ADMIN)).body.decode()
    assert leg.leg_code in body


def test_grid_route_pairs_distinguishes_no_criterion_from_no_route():
    """``None`` (rien ne borne) et l'ensemble vide (rien ne passe) ne se
    confondent pas : les confondre proposerait tous les voyages sur une grille
    qui ne sait en coter aucun."""
    from app.routers.commercial_router import grid_route_pairs

    assert grid_route_pairs(None) is None
    default = RateGrid(reference="RGD", client_id=None, is_default=True)
    default.lines = []
    assert grid_route_pairs(default) is None

    client_grid = RateGrid(reference="RG", client_id=10, is_default=False)
    client_grid.lines = []
    assert grid_route_pairs(client_grid) == set()

    client_grid.lines = [RateGridLine(pol_locode="frfec", pod_locode="brsso")]
    # LOCODE comparés en majuscules : une saisie en minuscules ne doit pas
    # faire disparaître les voyages de la route.
    assert grid_route_pairs(client_grid) == {("FRFEC", "BRSSO")}


# ────────── 4. Aucun prix pris sur une route arbitraire ──────────


@pytest.mark.asyncio
async def test_offer_creation_refuses_a_grid_that_does_not_cover_the_route(db):
    """Le défaut le plus grave : l'offre était cotée sur ``grid.lines[0]``.

    Un tarif qu'aucune route ne justifie n'est pas un repli, c'est une erreur
    silencieuse — et c'est ce prix qui part sur la booking note. La cascade rend
    le cas difficile à atteindre depuis l'écran ; le POST doit le refuser quand
    même (formulaire rejoué, appel direct).
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
