"""Navigation — comparaison de tous les voyages d'une même route POL→POD.

Superposer plusieurs passages sur une seule carte est ce qui rend un écart de
trajet visible : un détour, un contournement de dépression ou une trace
incomplète se repèrent par différence entre voyages, pas dans l'absolu.

Les règles vérifiées ici sont celles qui décident de ce que la carte **compare**,
et qui pourraient sinon mentir en silence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from starlette.datastructures import QueryParams

from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.voyage_track import (
    MAX_ROUTE_LEGS,
    legs_on_route,
    parse_route_key,
    route_key,
    route_spread,
    routes_served,
)

D0 = datetime(2026, 1, 1, tzinfo=UTC)


def _traced_leg_codes(html: str) -> set[str]:
    """Codes des legs réellement tracés sur la carte, lus dans ``data-legs``."""
    import html as _html
    import json
    import re

    m = re.search(r"data-legs='([^']*)'", html)
    if not m:
        return set()
    return {entry["leg_code"] for entry in json.loads(_html.unescape(m.group(1)))}


async def _referentials(db):
    db.add_all(
        [
            Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"),
            Vessel(id=2, code="ART", name="Artemis", imo_number="9876544", flag="FR"),
            Port(id=1, locode="FRFEC", name="Fécamp", country="FR"),
            Port(id=2, locode="BRSSO", name="São Sebastião", country="BR"),
            Port(id=3, locode="FRLEH", name="Le Havre", country="FR"),
        ]
    )
    await db.flush()


async def _leg(db, *, leg_id, code, pol=1, pod=2, vessel=1, days_offset=0, sailed=True, **kw):
    etd = D0 + timedelta(days=days_offset)
    leg = Leg(
        id=leg_id,
        leg_code=code,
        vessel_id=vessel,
        departure_port_id=pol,
        arrival_port_id=pod,
        etd_ref=etd,
        eta_ref=etd + timedelta(days=25),
        etd=etd,
        eta=etd + timedelta(days=25),
        atd=etd if sailed else None,
        **kw,
    )
    db.add(leg)
    await db.flush()
    return leg


# ───────────────────────────── Clé de route ─────────────────────────────


def test_route_key_is_directional_and_normalised():
    """L'aller et le retour sont deux routes : ni la même météo, ni la même durée."""
    assert route_key(" frfec ", "brsso") == "FRFEC-BRSSO"
    assert route_key("BRSSO", "FRFEC") == "BRSSO-FRFEC"
    assert route_key("FRFEC", "BRSSO") != route_key("BRSSO", "FRFEC")
    assert route_key("FRFEC", None) is None


def test_parse_route_key_refuses_a_malformed_key():
    """Une clé bricolée doit être refusée, pas devenir un filtre vide silencieux."""
    assert parse_route_key("frfec-BRSSO") == ("FRFEC", "BRSSO")
    assert parse_route_key("FRFEC") is None
    assert parse_route_key("FRFEC-BR") is None
    assert parse_route_key("FR FEC-BRSSO") is None
    assert parse_route_key(None) is None


# ──────────────────────── Routes réellement parcourues ───────────────────


@pytest.mark.asyncio
async def test_routes_served_lists_only_routes_actually_sailed(db):
    """Une route sans voyage parti n'a pas de trace : la proposer promettrait
    une carte vide."""
    await _referentials(db)
    await _leg(db, leg_id=1, code="1AFRBR6")
    await _leg(db, leg_id=2, code="1BFRBR6", days_offset=40)
    # Route planifiée mais jamais partie → absente.
    await _leg(db, leg_id=3, code="1CFRFR6", pod=3, sailed=False)

    routes = await routes_served(db)
    keys = {r["key"] for r in routes}
    assert "FRFEC-BRSSO" in keys
    assert "FRFEC-FRLEH" not in keys
    frbr = next(r for r in routes if r["key"] == "FRFEC-BRSSO")
    assert frbr["leg_count"] == 2
    assert frbr["pol_name"] == "Fécamp"
    assert frbr["pod_country"] == "BR"


@pytest.mark.asyncio
async def test_routes_served_keeps_both_directions_apart(db):
    await _referentials(db)
    await _leg(db, leg_id=1, code="1AFRBR6", pol=1, pod=2)
    await _leg(db, leg_id=2, code="1BBRFR6", pol=2, pod=1, days_offset=30)

    keys = {r["key"] for r in await routes_served(db)}
    assert keys == {"FRFEC-BRSSO", "BRSSO-FRFEC"}


@pytest.mark.asyncio
async def test_routes_served_includes_towt_archives(db):
    """Les archives sont justement les passages auxquels on compare (ADR-014)."""
    await _referentials(db)
    await _leg(db, leg_id=1, code="1YMB4", origin=LEG_ORIGIN_TOWT)

    routes = await routes_served(db)
    assert routes and routes[0]["key"] == "FRFEC-BRSSO"


@pytest.mark.asyncio
async def test_routes_are_ordered_by_traffic(db):
    """Les routes les plus fréquentées d'abord : c'est là qu'une comparaison
    a du sens."""
    await _referentials(db)
    await _leg(db, leg_id=1, code="1ABRFR6", pol=2, pod=1)
    for i, code in enumerate(("1AFRBR6", "1BFRBR6", "1CFRBR6")):
        await _leg(db, leg_id=10 + i, code=code, days_offset=i * 30)

    routes = await routes_served(db)
    assert routes[0]["key"] == "FRFEC-BRSSO"
    assert routes[0]["leg_count"] == 3


# ────────────────────────── Legs d'une route ─────────────────────────────


@pytest.mark.asyncio
async def test_legs_on_route_spans_vessels_and_years(db):
    """Le filtre est transverse au navire et à l'année — sinon il supprimerait
    les points de comparaison qu'on cherche."""
    await _referentials(db)
    await _leg(db, leg_id=1, code="1AFRBR6", vessel=1)
    await _leg(db, leg_id=2, code="2AFRBR7", vessel=2, days_offset=400)

    legs, total = await legs_on_route(db, "FRFEC", "BRSSO")
    assert total == 2
    assert {lg.id for lg in legs} == {1, 2}
    # Du plus récent au plus ancien : on compare au dernier passage.
    assert [lg.id for lg in legs] == [2, 1]


@pytest.mark.asyncio
async def test_legs_on_route_is_case_insensitive(db):
    await _referentials(db)
    await _leg(db, leg_id=1, code="1AFRBR6")
    legs, total = await legs_on_route(db, "frfec", "brsso")
    assert total == 1 and len(legs) == 1


@pytest.mark.asyncio
async def test_legs_on_route_caps_and_reports_the_total(db):
    """L'écran doit pouvoir dire « 10 sur 23 » : montrer les dix plus récents
    sans le dire laisserait croire que la route n'a connu que dix voyages."""
    await _referentials(db)
    for i in range(MAX_ROUTE_LEGS + 3):
        await _leg(db, leg_id=100 + i, code=f"1X{i:02d}", days_offset=i * 30)

    legs, total = await legs_on_route(db, "FRFEC", "BRSSO")
    assert total == MAX_ROUTE_LEGS + 3
    assert len(legs) == MAX_ROUTE_LEGS


# ─────────────────────────── Dispersion des trajets ──────────────────────


def _metrics(actual_nm, *, arrived=True, theoretical=5000.0):
    """Métrique minimale : seules les clés lues par ``route_spread``."""
    return SimpleNamespace(
        actual_nm=actual_nm,
        theoretical_nm=theoretical,
        declared_arrived=arrived,
        arrival_contradicted_by_track=(
            arrived and actual_nm > 0 and theoretical > 0 and actual_nm < theoretical
        ),
    )


def test_route_spread_summarises_the_gap_between_passages():
    spread = route_spread([_metrics(5200.0), _metrics(5800.0), _metrics(5500.0)])
    assert spread["count"] == 3
    assert spread["min_nm"] == 5200.0
    assert spread["max_nm"] == 5800.0
    assert spread["median_nm"] == 5500.0
    assert spread["spread_nm"] == 600.0
    assert round(spread["spread_pct"]) == 11


def test_route_spread_ignores_voyages_still_under_way():
    """Un voyage en cours a une distance partielle : l'inclure ferait passer un
    trajet inachevé pour un trajet court."""
    spread = route_spread([_metrics(5200.0), _metrics(5800.0), _metrics(900.0, arrived=False)])
    assert spread["count"] == 2
    assert spread["min_nm"] == 5200.0


def test_route_spread_ignores_a_track_that_contradicts_its_arrival():
    """Cas constaté le 2026-09-04 : arrivée déclarée après 119 NM relevés sur
    6287. Ni la trace ni l'arrivée ne sont exploitables — la moyenne non plus."""
    spread = route_spread([_metrics(5200.0), _metrics(5800.0), _metrics(119.0)])
    assert spread["count"] == 2


def test_route_spread_needs_at_least_two_voyages():
    """Une dispersion sur un seul point n'existe pas."""
    assert route_spread([_metrics(5200.0)]) is None
    assert route_spread([]) is None


# ────────────────────────────── Écran ────────────────────────────────────


@pytest.mark.asyncio
async def test_navigation_index_selects_every_leg_of_the_route(db):
    """La route pilote la sélection : elle remplace les legs cochés à la main."""
    from app.routers.navigation_router import navigation_index
    from tests.integration.conftest import FakeRequest

    await _referentials(db)
    await _leg(db, leg_id=1, code="1AFRBR6")
    await _leg(db, leg_id=2, code="1BFRBR6", days_offset=40)
    await _leg(db, leg_id=3, code="1CBRFR6", pol=2, pod=1, days_offset=80)

    request = FakeRequest()
    # La route lit ``query_params.getlist("leg_id")`` : un dict nu n'en a pas.
    request.query_params = QueryParams()
    resp = await navigation_index(
        request,
        route="FRFEC-BRSSO",
        db=db,
        user=SimpleNamespace(id=1, full_name="A", username="a", role="administrateur"),
    )
    # On regarde la **charge de la carte** (`data-legs`), pas la page entière :
    # les chips navire/année listent aussi le retour, et c'est normal — elles
    # sont le sélecteur manuel, indépendant du filtre de route.
    body = resp.body.decode()
    traced = _traced_leg_codes(body)
    assert traced == {"1AFRBR6", "1BFRBR6"}
    # Le retour n'est pas la même route : il ne doit pas être tracé.
    assert "1CBRFR6" not in traced


@pytest.mark.asyncio
async def test_navigation_index_ignores_a_malformed_route(db):
    """Une clé invalide ne doit pas vider la page en silence — on retombe sur
    le mode manuel, sans sélection."""
    from app.routers.navigation_router import navigation_index
    from tests.integration.conftest import FakeRequest

    await _referentials(db)
    await _leg(db, leg_id=1, code="1AFRBR6")

    request = FakeRequest()
    # La route lit ``query_params.getlist("leg_id")`` : un dict nu n'en a pas.
    request.query_params = QueryParams()
    resp = await navigation_index(
        request,
        route="n-importe-quoi",
        db=db,
        user=SimpleNamespace(id=1, full_name="A", username="a", role="administrateur"),
    )
    assert resp.status_code == 200
    assert "Comparer une route" in resp.body.decode()


def test_navigation_template_exposes_the_route_tool():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/navigation/index.html")[0]
    assert 'name="route"' in src
    assert "Comparer une route" in src
    assert "searchable-select.js" in src
