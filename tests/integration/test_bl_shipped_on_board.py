"""Date de mise à bord — dérivée de l'escale, corrigeable sous justification.

Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` §5.0.

Ce que ces tests protègent, par ordre de gravité :

1. **🔴 la dérivation ne lit que le RÉEL.** Dériver d'une opération *planifiée*
   produirait un connaissement **post-daté** — la fraude documentaire même que le
   §5.0 cherche à empêcher, et une exclusion de garantie ;
2. **pas de donnée source ⇒ pas de date.** Une escale sans opération réelle ne doit
   pas faire apparaître une date inventée sur un titre de propriété ;
3. **la justification est obligatoire**, refusée avant toute écriture, et exigée
   **en base** par une contrainte — aucun chemin d'écriture ne peut la contourner ;
4. **la dérivée n'est jamais figée dans la colonne d'override**, sinon « corrigé »
   et « pas corrigé » deviennent indistinguables ;
5. **un BL signé ne se corrige plus** : la date fait partie du document opposable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.services import bl_workflow as w
from app.services.derived_override import JustificationRequired

MOTIF = "Dernière palette embarquée le 14 au soir, SOF saisi le lendemain"


async def _ctx(db):
    from app.models.commercial import Client, Order
    from app.models.leg import Leg
    from app.models.packing_list import PackingList, PackingListBatch
    from app.models.port import Port
    from app.models.user import User
    from app.models.vessel import Vessel

    v = Vessel(name="Anemos", code="1")
    pol = Port(locode="FRFEC", name="Fécamp", country="FR")
    pod = Port(locode="BRSSO", name="Santos", country="BR")
    db.add_all([v, pol, pod])
    await db.flush()
    base = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    leg = Leg(
        leg_code="1CFRBR6",
        vessel_id=v.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    cl = Client(name="ACME", client_type="shipper")
    db.add_all([leg, cl])
    await db.flush()
    order = Order(reference="CMD-SOB", client_id=cl.id, leg_id=leg.id)
    db.add(order)
    await db.flush()
    pl = PackingList(order_id=order.id, leg_id=leg.id)
    db.add(pl)
    await db.flush()
    batch = PackingListBatch(packing_list_id=pl.id, batch_number=1, pallet_count=4)
    ops = User(
        username="ops-sob", email="ops-sob@newtowt.test", hashed_password="x", role="operation"
    )
    db.add_all([batch, ops])
    await db.flush()
    return leg, pl, batch, ops


async def _operation(db, leg, *, actual_start=None, actual_end=None, planned_end=None):
    from app.models.escale import EscaleOperation

    op = EscaleOperation(
        leg_id=leg.id,
        operation_type="LOADING",
        action="LOAD",
        planned_start=planned_end - timedelta(hours=6) if planned_end else None,
        planned_end=planned_end,
        actual_start=actual_start,
        actual_end=actual_end,
    )
    db.add(op)
    await db.flush()
    return op


# ───────────────────────── dérivation ─────────────────────────


@pytest.mark.asyncio
async def test_the_derived_date_is_the_last_actual_operation(db):
    leg, _pl, _b, _ops = await _ctx(db)
    await _operation(db, leg, actual_end=datetime(2026, 8, 11, 17, 0, tzinfo=UTC))
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 19, 30, tzinfo=UTC))
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 9, 0, tzinfo=UTC))

    assert await w.derive_shipped_on_board(db, leg_id=leg.id) == date(2026, 8, 12)


@pytest.mark.asyncio
async def test_a_planned_only_operation_yields_no_date(db):
    """🔴 Le point le plus grave. Dériver du prévisionnel post-daterait le BL.

    Une opération planifiée le 20 mais non réalisée ne doit produire AUCUNE date :
    un connaissement portant une date de mise à bord future est une fraude
    documentaire.
    """
    leg, _pl, _b, _ops = await _ctx(db)
    await _operation(db, leg, planned_end=datetime(2026, 8, 20, 17, 0, tzinfo=UTC))

    assert await w.derive_shipped_on_board(db, leg_id=leg.id) is None


@pytest.mark.asyncio
async def test_actual_start_is_used_when_the_end_is_missing(db):
    """Opération commencée et non clôturée : le réel disponible reste le début."""
    leg, _pl, _b, _ops = await _ctx(db)
    await _operation(db, leg, actual_start=datetime(2026, 8, 13, 6, 0, tzinfo=UTC))

    assert await w.derive_shipped_on_board(db, leg_id=leg.id) == date(2026, 8, 13)


@pytest.mark.asyncio
async def test_no_operation_yields_no_date(db):
    leg, _pl, _b, _ops = await _ctx(db)
    assert await w.derive_shipped_on_board(db, leg_id=leg.id) is None


@pytest.mark.asyncio
async def test_another_legs_operations_are_ignored(db):
    leg, _pl, _b, _ops = await _ctx(db)
    from app.models.leg import Leg

    other = Leg(
        leg_code="2AFRBR6",
        vessel_id=leg.vessel_id,
        departure_port_id=leg.departure_port_id,
        arrival_port_id=leg.arrival_port_id,
        etd_ref=leg.etd,
        eta_ref=leg.eta,
        etd=leg.etd,
        eta=leg.eta,
    )
    db.add(other)
    await db.flush()
    await _operation(db, other, actual_end=datetime(2026, 8, 30, 12, 0, tzinfo=UTC))

    assert await w.derive_shipped_on_board(db, leg_id=leg.id) is None


# ───────────────────────── résolution ─────────────────────────


@pytest.mark.asyncio
async def test_without_an_override_the_derived_date_is_used_and_says_so(db):
    leg, _pl, batch, _ops = await _ctx(db)
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 19, 0, tzinfo=UTC))

    r = await w.resolve_shipped_on_board(db, batch=batch, leg_id=leg.id)
    assert r.value == date(2026, 8, 12)
    assert r.is_override is False


@pytest.mark.asyncio
async def test_the_derived_value_is_never_frozen_into_the_override_column(db):
    """🔴 Lire la date ne doit pas la figer.

    Si la lecture recopiait la dérivée dans `bl_sob_date`, « corrigé volontairement
    à cette valeur » deviendrait indistinguable de « pas corrigé » — et la valeur
    deviendrait fausse dès que la timeline d'escale bouge.
    """
    from app.models.packing_list import PackingListBatch

    leg, _pl, batch, _ops = await _ctx(db)
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 19, 0, tzinfo=UTC))

    await w.resolve_shipped_on_board(db, batch=batch, leg_id=leg.id)
    fresh = await db.get(PackingListBatch, batch.id)
    assert fresh.bl_sob_date is None, "la lecture a figé la date dérivée"
    assert fresh.bl_sob_reason is None

    # Et la dérivée SUIT la donnée source : une opération plus tardive la déplace.
    await _operation(db, leg, actual_end=datetime(2026, 8, 15, 8, 0, tzinfo=UTC))
    r = await w.resolve_shipped_on_board(db, batch=batch, leg_id=leg.id)
    assert r.value == date(2026, 8, 15)


# ───────────────────────── override ─────────────────────────


@pytest.mark.asyncio
async def test_an_override_without_a_reason_is_refused_before_any_write(db):
    """🔴 Refusé AVANT écriture : un override sans motif ne doit pas exister,
    même transitoirement."""
    from app.models.packing_list import PackingListBatch

    leg, _pl, batch, ops = await _ctx(db)
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 19, 0, tzinfo=UTC))

    with pytest.raises(JustificationRequired):
        await w.override_shipped_on_board(
            db, batch=batch, leg_id=leg.id, new_date=date(2026, 8, 14), reason="", user=ops
        )
    fresh = await db.get(PackingListBatch, batch.id)
    assert fresh.bl_sob_date is None, "la date a été écrite malgré le refus"


@pytest.mark.asyncio
async def test_a_hollow_reason_is_refused(db):
    leg, _pl, batch, ops = await _ctx(db)
    with pytest.raises(JustificationRequired):
        await w.override_shipped_on_board(
            db, batch=batch, leg_id=leg.id, new_date=date(2026, 8, 14), reason="erreur", user=ops
        )


@pytest.mark.asyncio
async def test_a_justified_override_wins_and_is_traced_with_a_snapshot(db):
    from app.models.activity_log import ActivityLog

    leg, _pl, batch, ops = await _ctx(db)
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 19, 0, tzinfo=UTC))

    r = await w.override_shipped_on_board(
        db, batch=batch, leg_id=leg.id, new_date=date(2026, 8, 14), reason=MOTIF, user=ops
    )
    assert r.value == date(2026, 8, 14)
    assert r.is_override is True and r.diverges is True
    assert batch.bl_sob_by_id == ops.id and batch.bl_sob_at is not None

    logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "bl_shipped_on_board_overridden")
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    detail = logs[0].detail or ""
    # Le journal doit contenir l'ancienne valeur, la nouvelle, la dérivée et le motif :
    # un contrôle des mois plus tard doit pouvoir rejouer le raisonnement.
    assert "2026-08-12" in detail and "2026-08-14" in detail
    assert MOTIF in detail
    assert '"source": "override"' in detail


@pytest.mark.asyncio
async def test_the_reason_is_stored_normalised(db):
    leg, _pl, batch, ops = await _ctx(db)
    await w.override_shipped_on_board(
        db,
        batch=batch,
        leg_id=leg.id,
        new_date=date(2026, 8, 14),
        reason="  Dernière   palette\n embarquée le 14  ",
        user=ops,
    )
    assert batch.bl_sob_reason == "Dernière palette embarquée le 14"


@pytest.mark.asyncio
async def test_overriding_a_signed_bl_is_refused(db):
    """La date fait partie du document opposable : après signature, révision."""
    from app.models.client_account import ClientAccount
    from app.models.user import User

    leg, pl, batch, ops = await _ctx(db)
    client = ClientAccount(email="c-sob@example.test", hashed_password="x", company_name="Belco")
    master = User(
        username="cdt-sob", email="cdt-sob@newtowt.test", hashed_password="x", role="marins"
    )
    db.add_all([client, master])
    await db.flush()
    await w.generate_draft(db, pl=pl, batch=batch, leg=leg, user=ops)
    await w.validate_by_client(db, batch=batch, client=client)
    await w.sign_by_master(db, batch=batch, user=master)

    with pytest.raises(w.BlFrozen):
        await w.override_shipped_on_board(
            db, batch=batch, leg_id=leg.id, new_date=date(2026, 8, 14), reason=MOTIF, user=ops
        )
    assert batch.bl_sob_date is None


@pytest.mark.asyncio
async def test_the_database_itself_refuses_a_date_without_a_reason(db):
    """⚠️ Le garde-fou ultime : la contrainte, pas seulement le service.

    Un futur chemin d'écriture qui oublierait de passer par
    `override_shipped_on_board` doit malgré tout échouer.
    """
    from sqlalchemy.exc import IntegrityError

    _leg, _pl, batch, _ops = await _ctx(db)
    batch.bl_sob_date = date(2026, 8, 14)
    batch.bl_sob_reason = None
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_a_batch_without_a_leg_resolves_to_nothing(db):
    """Pas de leg, pas de timeline, donc pas de date — et pas d'exception."""
    _leg, _pl, batch, _ops = await _ctx(db)
    r = await w.resolve_shipped_on_board(db, batch=batch, leg_id=None)
    assert r.value is None


# ───────────────────────── la route Opérations ─────────────────────────


class _FormReq:
    """Requête minimale portant un formulaire."""

    def __init__(self, form: dict):
        self._form = form
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="203.0.113.44")
        self.url = SimpleNamespace(path="/cargo")

    async def form(self):
        return self._form


@pytest.mark.asyncio
async def test_the_route_refuses_an_override_without_a_reason(db):
    """400 : donnée d'entrée invalide, pas une panne — l'utilisateur doit corriger."""
    from fastapi import HTTPException

    from app.routers.cargo_packing_router import override_bl_shipped_on_board

    leg, pl, batch, ops = await _ctx(db)
    req = _FormReq({"shipped_on_board": "2026-08-14", "reason": ""})
    with pytest.raises(HTTPException) as e:
        await override_bl_shipped_on_board(pl.id, batch.id, req, db=db, user=ops)
    assert e.value.status_code == 400
    assert batch.bl_sob_date is None


@pytest.mark.asyncio
async def test_the_route_refuses_a_malformed_date(db):
    from fastapi import HTTPException

    from app.routers.cargo_packing_router import override_bl_shipped_on_board

    leg, pl, batch, ops = await _ctx(db)
    req = _FormReq({"shipped_on_board": "14/08/2026", "reason": MOTIF})
    with pytest.raises(HTTPException) as e:
        await override_bl_shipped_on_board(pl.id, batch.id, req, db=db, user=ops)
    assert e.value.status_code == 400
    assert "AAAA-MM-JJ" in str(e.value.detail)


@pytest.mark.asyncio
async def test_the_route_applies_a_justified_override(db):
    from app.routers.cargo_packing_router import override_bl_shipped_on_board

    leg, pl, batch, ops = await _ctx(db)
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 19, 0, tzinfo=UTC))
    req = _FormReq({"shipped_on_board": "2026-08-14", "reason": MOTIF})
    resp = await override_bl_shipped_on_board(pl.id, batch.id, req, db=db, user=ops)
    assert resp.status_code == 303
    assert batch.bl_sob_date == date(2026, 8, 14)
    assert batch.bl_sob_reason == MOTIF


def test_the_override_route_is_a_post_requiring_modify():
    """La déclaration protège : la corriger en GET ou en `cargo:C` casse la suite."""
    import inspect

    from app.routers import cargo_packing_router as r

    route = next(
        rt for rt in r.router.routes if getattr(rt, "path", "").endswith("/bl/shipped-on-board")
    )
    assert route.methods == {"POST"}
    assert 'require_permission("cargo", "M")' in inspect.getsource(r.override_bl_shipped_on_board)


# ───────────────────────── la date sur le document ─────────────────────────


@pytest.mark.asyncio
async def test_the_pdf_shows_the_derived_date(db):
    """La mention doit atteindre le document, sinon le mécanisme n'existe pas.

    Rendu réel du gabarit, seule la conversion WeasyPrint étant substituée.
    """
    import app.services.pdf_generator as gen
    from app.templating import templates

    leg, pl, batch, ops = await _ctx(db)
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 19, 0, tzinfo=UTC))
    await w.generate_draft(db, pl=pl, batch=batch, leg=leg, user=ops)
    sob = await w.resolve_shipped_on_board(db, batch=batch, leg_id=leg.id)

    def _html_only(template: str, context: dict) -> tuple[str, bytes]:
        return templates.get_template(template).render(**context), b"%PDF"

    original = gen._render_pdf
    gen._render_pdf = _html_only
    try:
        html = gen.render_bill_of_lading_from_pl(
            pl=pl,
            batch=batch,
            leg=leg,
            vessel=None,
            pol=None,
            pod=None,
            bl_number=batch.bl_number,
            shipped_on_board=sob,
        ).html
    finally:
        gen._render_pdf = original

    assert "Shipped on board" in html
    assert "12/08/2026" in html


@pytest.mark.asyncio
async def test_the_pdf_says_not_established_rather_than_inventing_a_date(db):
    """🔴 Aucune opération réelle ⇒ la mention le dit, elle n'invente pas."""
    import app.services.pdf_generator as gen
    from app.templating import templates

    leg, pl, batch, ops = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=leg, user=ops)
    sob = await w.resolve_shipped_on_board(db, batch=batch, leg_id=leg.id)
    assert sob.value is None

    def _html_only(template: str, context: dict) -> tuple[str, bytes]:
        return templates.get_template(template).render(**context), b"%PDF"

    original = gen._render_pdf
    gen._render_pdf = _html_only
    try:
        html = gen.render_bill_of_lading_from_pl(
            pl=pl,
            batch=batch,
            leg=leg,
            vessel=None,
            pol=None,
            pod=None,
            bl_number=batch.bl_number,
            shipped_on_board=sob,
        ).html
    finally:
        gen._render_pdf = original

    assert "non constatée" in html


def test_the_staff_screen_exposes_the_correction_form():
    """Une route sans point d'entrée n'existe pas pour les Opérations."""
    import re as _re

    from app.templating import templates

    raw = templates.env.loader.get_source(templates.env, "staff/cargo/packing_list_detail.html")[0]
    src = _re.sub(r"\{#.*?#\}", "", raw, flags=_re.DOTALL)

    assert "bl/shipped-on-board" in src
    # Le motif est exigé côté formulaire AUSSI (le service reste l'autorité).
    assert 'name="reason" required' in src
    # La provenance est affichée : « corrigée » ne doit pas se confondre avec
    # « dérivée de l'escale ».
    assert "dérivée de l'escale" in src
    assert "corrigée" in src


@pytest.mark.asyncio
async def test_the_detail_screen_resolves_the_date_for_each_batch(db):
    """Le contexte doit porter la date résolue — sinon le gabarit n'affiche rien."""
    from app.routers.cargo_packing_router import packing_list_detail

    leg, pl, batch, ops = await _ctx(db)
    await _operation(db, leg, actual_end=datetime(2026, 8, 12, 19, 0, tzinfo=UTC))
    await w.generate_draft(db, pl=pl, batch=batch, leg=leg, user=ops)

    resp = await packing_list_detail(pl.id, _DetailReq(), db=db, user=ops)
    sob = resp.context["sob_by_batch"][batch.id]
    assert sob.value == date(2026, 8, 12)
    assert sob.is_override is False


class _DetailReq:
    """Requête minimale pour un rendu de gabarit."""

    headers: dict[str, str] = {}
    client = SimpleNamespace(host="203.0.113.44")
    url = SimpleNamespace(path="/cargo")
    state = SimpleNamespace(csrf_token="x")
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
