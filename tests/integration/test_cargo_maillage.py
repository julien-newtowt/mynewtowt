"""Maillage cargo ↔ voyage — Phase 2 reprise UX (docs/design/03-reprise-ux-legacy.md
§9.2, constats M-A/B/C/D/I).

Couvre :
(a) le bandeau de contexte voyage sur la fiche packing list (liens escale/bord) ;
(b) la colonne Voyage sur l'index des packing lists ;
(c) le filtre fonctionnel ``?leg_id=`` de l'index (résolution COM-11) ;
(d) le lien « Fiche PL » depuis l'écran BL commandant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.commercial import Client, Order
from app.models.leg import Leg
from app.models.packing_list import PackingList
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    """Requête minimale pour appeler la coroutine de route hors ASGI."""

    def __init__(self, leg_id: int | None = None):
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.url = SimpleNamespace(path="/cargo/packing-lists")
        self.state = SimpleNamespace(csrf_token="x")
        self.cookies: dict[str, str] = {}
        self.query_params: dict[str, str] = {"leg_id": str(leg_id)} if leg_id else {}


async def _setup_graph(db):
    """Client + navire + 2 ports + leg + 2 commandes + 2 packing lists.

    ``pl_on_leg`` est rattachée (via sa commande) au leg créé ; ``pl_off_leg``
    provient d'une commande sans leg — PL "orpheline" pour le maillage.
    """
    db.add(Client(id=1, name="ACME", client_type="shipper"))
    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(
        id=1,
        leg_code="1CFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    db.add(leg)
    await db.flush()

    order_on_leg = Order(id=1, reference="OT-2026-0001", client_id=1, leg_id=leg.id)
    order_off_leg = Order(id=2, reference="OT-2026-0002", client_id=1, leg_id=None)
    db.add_all([order_on_leg, order_off_leg])
    await db.flush()

    pl_on_leg = PackingList(order_id=order_on_leg.id, token="tok_maillage_0001", status="draft")
    pl_off_leg = PackingList(order_id=order_off_leg.id, token="tok_maillage_0002", status="draft")
    db.add_all([pl_on_leg, pl_off_leg])
    await db.flush()
    return leg, pl_on_leg, pl_off_leg


# ───────────────────── (a) bandeau de contexte — fiche PL ─────────────────────


def test_packing_list_detail_template_has_the_voyage_banner():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/cargo/packing_list_detail.html")[0]
    assert "/escale?leg_id=" in src
    assert "/captain?leg_id=" in src
    assert "/captain/bl?leg_id=" in src
    assert "/stowage/legs/" in src
    # Pas de bandeau pour une PL orpheline (aucun leg résolu).
    assert "{% if leg %}" in src
    # Référence de commande cliquable.
    assert "/commercial/orders/{{ order.id }}" in src
    assert "/cargo/booking/{{ booking.reference }}" in src


@pytest.mark.asyncio
async def test_packing_list_detail_exposes_leg_vessel_pol_pod(db):
    from app.routers.cargo_packing_router import packing_list_detail

    leg, pl_on_leg, _pl_off_leg = await _setup_graph(db)
    user = SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")

    resp = await packing_list_detail(pl_on_leg.id, _Req(), db=db, user=user)
    assert resp.context["leg"].id == leg.id
    assert resp.context["vessel"].name == "Anemos"
    assert resp.context["pol"].locode == "FRFEC"
    assert resp.context["pod"].locode == "BRSSO"


@pytest.mark.asyncio
async def test_packing_list_detail_has_no_leg_for_an_orphan_pl(db):
    """Aucun leg résolu (commande sans leg) ⇒ pas de bandeau côté template."""
    from app.routers.cargo_packing_router import packing_list_detail

    _leg, _pl_on_leg, pl_off_leg = await _setup_graph(db)
    user = SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")

    resp = await packing_list_detail(pl_off_leg.id, _Req(), db=db, user=user)
    assert resp.context["leg"] is None


# ───────────────────── (b)/(c) index des packing lists ─────────────────────


def test_packing_lists_index_template_has_the_voyage_and_vessel_columns():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/cargo/packing_lists.html")[0]
    assert "<th>Voyage</th>" in src
    assert "<th>Navire</th>" in src
    assert "Filtré sur le voyage" in src
    assert "retirer le filtre" in src


@pytest.mark.asyncio
async def test_index_resolves_legs_in_one_grouped_query_not_per_row(db, monkeypatch):
    """Pas de N+1 : une seule requête groupée pour résoudre les legs des PL affichées."""
    from app.routers.cargo_packing_router import packing_lists_index

    await _setup_graph(db)
    user = SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")

    original_execute = db.execute
    calls: list[str] = []

    async def _counting_execute(stmt, *a, **kw):
        calls.append(str(stmt))
        return await original_execute(stmt, *a, **kw)

    monkeypatch.setattr(db, "execute", _counting_execute)
    resp = await packing_lists_index(_Req(), leg_id=None, db=db, user=user)
    assert len(resp.context["packing_lists"]) == 2
    # 1 requête PL (jointe Order/Booking) + 1 requête Leg/Vessel groupée
    # (+ la requête de comptage des messages non lus) — jamais une par PL.
    leg_queries = [c for c in calls if '"legs"' in c or "legs " in c]
    assert len(leg_queries) <= 1, f"résolution des legs non groupée : {leg_queries}"


@pytest.mark.asyncio
async def test_index_filter_by_leg_id_keeps_only_the_matching_pl(db):
    from app.routers.cargo_packing_router import packing_lists_index

    leg, pl_on_leg, pl_off_leg = await _setup_graph(db)
    user = SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")

    resp = await packing_lists_index(_Req(leg.id), leg_id=leg.id, db=db, user=user)
    listed_ids = {pl.id for pl in resp.context["packing_lists"]}
    assert listed_ids == {pl_on_leg.id}
    assert pl_off_leg.id not in listed_ids
    assert resp.context["filter_leg"].id == leg.id
    assert resp.context["filter_leg_id"] == leg.id


@pytest.mark.asyncio
async def test_index_without_filter_lists_both_and_resolves_leg_by_pl(db):
    from app.routers.cargo_packing_router import packing_lists_index

    leg, pl_on_leg, pl_off_leg = await _setup_graph(db)
    user = SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")

    resp = await packing_lists_index(_Req(), leg_id=None, db=db, user=user)
    listed_ids = {pl.id for pl in resp.context["packing_lists"]}
    assert listed_ids == {pl_on_leg.id, pl_off_leg.id}
    assert resp.context["leg_id_by_pl"][pl_on_leg.id] == leg.id
    assert resp.context["leg_id_by_pl"][pl_off_leg.id] is None
    assert resp.context["leg_and_vessel_by_leg_id"][leg.id][0].leg_code == "1CFRBR6"
    assert resp.context["leg_and_vessel_by_leg_id"][leg.id][1].name == "Anemos"
    assert resp.context["filter_leg"] is None


# ───────────────────── (d) écran BL commandant → fiche PL ─────────────────────


def test_bl_list_template_links_to_the_packing_list_sheet():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/captain/bl_list.html")[0]
    assert '/cargo/packing-lists/{{ b.packing_list_id }}"' in src
    assert "Fiche PL" in src
