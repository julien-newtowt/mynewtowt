"""Cargo — reprise UX Phase 3 (docs/design/03-reprise-ux-legacy.md §9.2,
constats M-1/2/3/G/H).

Couvre :
(a) marqueurs structurants de la fiche packing list (zone HTMX, sous-nav,
    saisie repliée) ;
(b) réponse de mutation HTMX (204 + ``cargoRefresh``) vs repli 303 classique
    du lock de packing list ;
(c) export Excel côté portail (token valide → xlsx, token invalide → 410) ;
(d) filtre leg auto-soumis (HTMX) sur l'écran BL commandant ;
(e) sobriété du portail (formulaire replié, pas de HTMX ajouté).
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
    """Requête minimale pour appeler une coroutine de route hors ASGI."""

    def __init__(self):
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.url = SimpleNamespace(path="/cargo/packing-lists")
        self.state = SimpleNamespace(csrf_token="x")
        self.cookies: dict[str, str] = {}
        self.query_params: dict[str, str] = {}


class _HxReq(_Req):
    def __init__(self):
        super().__init__()
        self.headers = {"hx-request": "true"}


async def _setup_pl(db) -> PackingList:
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
    order = Order(id=1, reference="OT-2026-0001", client_id=1, leg_id=leg.id)
    db.add(order)
    await db.flush()
    pl = PackingList(order_id=order.id, status="draft")
    db.add(pl)
    await db.flush()
    return pl


# ───────────────────── (a) marqueurs de la fiche packing list ─────────────────────


def test_packing_list_detail_template_htmx_markers():
    """Zone HTMX, sous-nav collante et saisie repliée sont bien posées."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/cargo/packing_list_detail.html")[0]
    # Zone rafraîchie sans rechargement (pattern #escale-sections, Phase 1).
    assert 'id="pl-sections"' in src
    assert "cargoRefresh" in src
    assert 'hx-select="#pl-sections"' in src
    # Sous-navigation collante — classe existante, réutilisée.
    assert "escale-subnav" in src
    assert "#sec-lots" in src
    assert "#sec-messagerie" in src
    # Saisie « ajouter un batch » repliée par défaut.
    assert "form-disclosure" in src


def test_packing_list_detail_template_leaves_sensitive_bl_actions_classic():
    """Les actions BL sensibles et les suppressions gardent leur redirect classique
    (pas de hx-post ajouté sur ces formulaires précis)."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/cargo/packing_list_detail.html")[0]
    assert 'action="/cargo/packing-lists/{{ pl.id }}/batches/{{ b.id }}/bl/draft"' in src
    # Le formulaire de génération de draft ne porte pas de hx-post (recherche
    # locale : la sous-chaîne qui suit immédiatement l'action ne doit pas être
    # un attribut hx-post).
    draft_form_idx = src.index("/bl/draft")
    snippet = src[draft_form_idx : draft_form_idx + 200]
    assert "hx-post" not in snippet


# ───────────────────── (b) mutation HTMX (204 + cargoRefresh) ─────────────────────


@pytest.mark.asyncio
async def test_lock_pl_htmx_returns_204_with_cargo_refresh(db, staff_user):
    from app.routers.cargo_packing_router import lock_pl

    pl = await _setup_pl(db)

    resp = await lock_pl(pl.id, _HxReq(), db=db, user=staff_user)
    assert resp.status_code == 204
    trigger = resp.headers["HX-Trigger"]
    assert "cargoRefresh" in trigger
    assert "toast" in trigger
    await db.refresh(pl)
    assert pl.status == "locked"


@pytest.mark.asyncio
async def test_lock_pl_without_htmx_redirects(db, staff_user):
    from app.routers.cargo_packing_router import lock_pl

    pl = await _setup_pl(db)

    resp = await lock_pl(pl.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/cargo/packing-lists/{pl.id}"


@pytest.mark.asyncio
async def test_add_batch_htmx_returns_204_with_cargo_refresh(db, staff_user):
    from app.routers.cargo_packing_router import add_batch

    pl = await _setup_pl(db)
    req = _HxReq()
    req._form = {"pallet_format": "EPAL", "pallet_count": "2"}

    async def _form():
        return req._form

    req.form = _form

    resp = await add_batch(pl.id, req, db=db, user=staff_user)
    assert resp.status_code == 204
    assert "cargoRefresh" in resp.headers["HX-Trigger"]


# ───────────────────── (c) export Excel côté portail ─────────────────────


@pytest.mark.asyncio
async def test_portal_packing_export_xlsx_valid_token(db):
    from app.routers.cargo_portal_router import portal_packing_export_xlsx

    pl = await _setup_pl(db)

    resp = await portal_packing_export_xlsx(pl.token, _Req(), db=db)
    assert resp.status_code == 200
    assert resp.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp.body  # contenu xlsx non vide


@pytest.mark.asyncio
async def test_portal_packing_export_xlsx_invalid_token(db):
    from fastapi import HTTPException

    from app.routers.cargo_portal_router import portal_packing_export_xlsx

    await _setup_pl(db)  # une PL existe, mais on interroge un token bidon

    with pytest.raises(HTTPException) as exc_info:
        await portal_packing_export_xlsx("tok_does_not_exist_0000000", _Req(), db=db)
    assert exc_info.value.status_code == 410


# ───────────────────── (d) filtre leg auto-soumis — écran BL commandant ─────────────────────


def test_bl_list_template_leg_select_is_htmx_auto_submit():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/captain/bl_list.html")[0]
    select_idx = src.index('<select name="leg_id"')
    select_tag = src[select_idx : src.index(">", select_idx)]
    assert 'hx-get="/captain/bl"' in select_tag
    assert 'hx-trigger="change"' in select_tag
    # Repli sans JS : le bouton « Afficher » reste présent.
    assert "Afficher" in src


# ───────────────────── (e) portail — sobriété (pas de HTMX ajouté) ─────────────────────


def test_portal_packing_template_has_export_and_form_disclosure_without_htmx():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "portal/packing.html")[0]
    assert "export.xlsx" in src
    assert "form-disclosure" in src
    # Portail sobre : aucun attribut HTMX introduit par cette reprise.
    assert "hx-post" not in src
    assert "hx-get" not in src
