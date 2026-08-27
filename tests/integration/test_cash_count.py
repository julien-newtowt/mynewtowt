"""Contrôle de caisse — état déclaré par le commandant et historisation des écarts.

À chaque fin d'embarquement et chaque fin de mois, le commandant sortant
déclare sa caisse coupure par coupure. Deux propriétés portent la valeur du
contrôle et sont verrouillées ici : le total n'est **jamais** repris du
formulaire (sinon un état de caisse ne contrôlerait rien), et l'écart est
**figé** au moment de la déclaration avec le solde théorique de ce moment.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.cash_count import CashCount, CashCountLine
from app.models.vessel import Vessel
from app.services import cash_count as svc
from app.services import cashbox as cashbox_svc


async def _box(db, *, code="ANE"):
    vessel = Vessel(code=code, name="Anemos")
    db.add(vessel)
    await db.flush()
    return vessel, await cashbox_svc.get_or_create(db, vessel.id)


async def _movement(db, cb, amount, *, currency="EUR", when=None, staff_user=None):
    return await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal(amount),
        currency=currency,
        category="depot_recharge" if Decimal(amount) > 0 else "avitaillement",
        description="mouvement de test",
        occurred_at=when,
        recorded_by_id=getattr(staff_user, "id", None),
    )


def test_denominations_follow_the_reference_sheet():
    """Le référentiel reprend le document type utilisé à bord."""
    eur = dict(svc.denominations_for("EUR"))
    # Billets présents sur la feuille, du plus fort au plus faible.
    assert [v for v, k in svc.denominations_for("EUR") if k == "billet"] == [
        Decimal("200"),
        Decimal("100"),
        Decimal("50"),
        Decimal("20"),
        Decimal("10"),
        Decimal("5"),
    ]
    # Le 500 € n'y figure pas : proposer une ligne qu'on ne peut pas remplir.
    assert Decimal("500") not in eur
    # Les pièces sont détaillées jusqu'au centime.
    assert eur[Decimal("0.01")] == "piece"
    usd = [v for v, k in svc.denominations_for("USD") if k == "billet"]
    assert usd == [Decimal(x) for x in ("100", "50", "20", "10", "5", "1")]
    # Le đồng n'a plus de pièces en circulation courante.
    assert all(k == "billet" for _v, k in svc.denominations_for("VND"))


def test_unknown_currency_is_refused():
    with pytest.raises(svc.CashCountError):
        svc.denominations_for("GBP")


@pytest.mark.asyncio
async def test_total_is_recomputed_from_the_denominations(db, staff_user):
    """Reproduit le bloc EUROS de la feuille type : 2×20 + 1×5 + 31,47 = 76,47."""
    _vessel, cb = await _box(db)
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
        counted_on=date(2026, 8, 31),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("20"): 2, Decimal("5"): 1}},
        bulk_coins={"EUR": Decimal("31.47")},
    )
    block = count.currencies[0]
    assert block.counted_total == Decimal("76.47")
    # Caisse vide côté mouvements : l'écart est le comptage lui-même.
    assert block.computed_balance == Decimal("0.00")
    assert block.variance == Decimal("76.47")


@pytest.mark.asyncio
async def test_dollar_block_matches_the_reference_sheet(db, staff_user):
    """29×100 + 12×50 + 12×20 + 9×10 + 5×5 + 12×1 = 3 867,00 $."""
    _vessel, cb = await _box(db)
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_embarquement",
        counted_on=date(2026, 8, 31),
        declared_by_name="Cdt Sortant",
        counts={
            "USD": {
                Decimal("100"): 29,
                Decimal("50"): 12,
                Decimal("20"): 12,
                Decimal("10"): 9,
                Decimal("5"): 5,
                Decimal("1"): 12,
            }
        },
    )
    assert count.currencies[0].counted_total == Decimal("3867.00")


@pytest.mark.asyncio
async def test_variance_against_the_theoretical_balance(db, staff_user):
    _vessel, cb = await _box(db)
    await _movement(db, cb, "100.00", staff_user=staff_user)
    await _movement(db, cb, "-25.50", staff_user=staff_user)  # théorique : 74,50
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("20"): 3, Decimal("10"): 1}},  # compté : 70,00
        variance_reasons={"EUR": "Avance équipage non saisie"},
    )
    block = count.currencies[0]
    assert block.computed_balance == Decimal("74.50")
    assert block.counted_total == Decimal("70.00")
    assert block.variance == Decimal("-4.50")
    assert block.variance_reason == "Avance équipage non saisie"
    assert count.has_variance is True


@pytest.mark.asyncio
async def test_a_later_movement_never_rewrites_a_past_control(db, staff_user):
    """L'écart est figé : c'est ce qui rend un contrôle opposable."""
    _vessel, cb = await _box(db)
    await _movement(db, cb, "100.00", staff_user=staff_user)
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_embarquement",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("100"): 1}},
    )
    assert count.currencies[0].variance == Decimal("0.00")

    # Un mouvement saisi après coup ne doit pas modifier le contrôle rendu.
    await _movement(db, cb, "-40.00", staff_user=staff_user)
    await db.refresh(count)
    assert count.currencies[0].computed_balance == Decimal("100.00")
    assert count.currencies[0].variance == Decimal("0.00")


@pytest.mark.asyncio
async def test_only_declared_currencies_produce_a_block(db, staff_user):
    """Un bloc à zéro sur une devise réellement détenue ferait un faux écart."""
    _vessel, cb = await _box(db)
    await _movement(db, cb, "500.00", currency="USD", staff_user=staff_user)
    count = await svc.declare_count(
        db,
        cb,
        trigger="controle",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("10"): 1}},
    )
    assert [b.currency for b in count.currencies] == ["EUR"]


@pytest.mark.asyncio
async def test_empty_denominations_are_not_stored(db, staff_user):
    """L'état reste lisible : une coupure absente est sans ambiguïté un zéro."""
    _vessel, cb = await _box(db)
    count = await svc.declare_count(
        db,
        cb,
        trigger="controle",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("50"): 2, Decimal("20"): 0}},
    )
    lines = (
        (
            await db.execute(
                select(CashCountLine).where(
                    CashCountLine.cash_count_currency_id == count.currencies[0].id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [line.denomination for line in lines] == [Decimal("50.00")]
    assert lines[0].line_total == Decimal("100.00")


@pytest.mark.asyncio
async def test_handover_names_the_outgoing_and_incoming_masters(db, staff_user):
    """Ce qui rend un écart imputable : la caisse a enfin un détenteur."""
    _vessel, cb = await _box(db)
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_embarquement",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="Cdt Sortant",
        handover_to_name="Cdt Entrant",
        declared_by_id=staff_user.id,
        counts={"EUR": {Decimal("5"): 1}},
        notes="Relève à Fécamp, comptage contradictoire",
    )
    assert count.declared_by_name == "Cdt Sortant"
    assert count.handover_to_name == "Cdt Entrant"
    assert count.declared_by_id == staff_user.id
    assert count.status == "declare"


@pytest.mark.asyncio
async def test_invalid_declarations_are_refused(db, staff_user):
    _vessel, cb = await _box(db)
    today = datetime.now(UTC).date()
    common = {"counted_on": today, "declared_by_name": "Cdt"}

    with pytest.raises(svc.CashCountError):  # motif inconnu
        await svc.declare_count(
            db, cb, trigger="au_pif", counts={"EUR": {Decimal("5"): 1}}, **common
        )
    with pytest.raises(svc.CashCountError):  # aucune devise
        await svc.declare_count(db, cb, trigger="controle", counts={}, **common)
    with pytest.raises(svc.CashCountError):  # coupure inexistante
        await svc.declare_count(
            db, cb, trigger="controle", counts={"EUR": {Decimal("37"): 1}}, **common
        )
    with pytest.raises(svc.CashCountError):  # nombre négatif
        await svc.declare_count(
            db, cb, trigger="controle", counts={"EUR": {Decimal("5"): -1}}, **common
        )
    with pytest.raises(svc.CashCountError):  # déclarant anonyme
        await svc.declare_count(
            db,
            cb,
            trigger="controle",
            counted_on=today,
            declared_by_name="   ",
            counts={"EUR": {Decimal("5"): 1}},
        )
    # Aucun état n'a été enregistré.
    assert await db.scalar(select(func.count()).select_from(CashCount)) == 0


@pytest.mark.asyncio
async def test_review_records_the_office_decision_once(db, staff_user):
    _vessel, cb = await _box(db)
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("5"): 1}},
    )
    await svc.review_count(
        db, count, status="valide", reviewed_by_id=staff_user.id, comment="Écart accepté"
    )
    assert count.status == "valide"
    assert count.reviewed_at is not None
    # Une suite déjà donnée ne se rejoue pas.
    with pytest.raises(svc.CashCountError):
        await svc.review_count(db, count, status="conteste")


@pytest.mark.asyncio
async def test_history_is_most_recent_first(db, staff_user):
    _vessel, cb = await _box(db)
    for day in (10, 20, 15):
        await svc.declare_count(
            db,
            cb,
            trigger="controle",
            counted_on=date(2026, 8, day),
            declared_by_name="Cdt",
            counts={"EUR": {Decimal("5"): 1}},
        )
    rows = await svc.history(db, cb)
    assert [c.counted_on.day for c in rows] == [20, 15, 10]
    assert (await svc.last_count(db, cb)).counted_on == date(2026, 8, 20)


# ── Route de déclaration ────────────────────────────────────────────────────


def _form_request(fields: dict[str, str]):
    """Requête Starlette portant un formulaire urlencodé."""
    from urllib.parse import urlencode

    from starlette.requests import Request

    body = urlencode(fields).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/cashbox/1/etat",
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body)).encode()),
        ],
        "query_string": b"",
    }
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_route_declares_only_the_checked_currencies(db, staff_user):
    from app.routers import cashbox_router as r

    vessel, _cb = await _box(db)
    resp = await r.submit_cash_count(
        _form_request(
            {
                "trigger": "fin_embarquement",
                "counted_on": "2026-08-31",
                "declared_by_name": "Cdt Sortant",
                "handover_to_name": "Cdt Entrant",
                "declare_EUR": "1",
                "qty_EUR_20": "2",
                "qty_EUR_5": "1",
                "bulk_EUR": "31,47",
                # USD non coché : les quantités saisies par erreur sont ignorées.
                "qty_USD_100": "29",
            }
        ),
        vessel.id,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    count = (await db.execute(select(CashCount))).scalars().unique().one()
    assert [b.currency for b in count.currencies] == ["EUR"]
    assert count.currencies[0].counted_total == Decimal("76.47")
    assert count.counted_on == date(2026, 8, 31)
    assert count.handover_to_name == "Cdt Entrant"


@pytest.mark.asyncio
async def test_route_refuses_an_invalid_quantity(db, staff_user):
    from fastapi import HTTPException

    from app.routers import cashbox_router as r

    vessel, _cb = await _box(db)
    for bad in ("-3", "deux", "1.5"):
        with pytest.raises(HTTPException) as exc:
            await r.submit_cash_count(
                _form_request(
                    {
                        "trigger": "controle",
                        "counted_on": "2026-08-31",
                        "declared_by_name": "Cdt",
                        "declare_EUR": "1",
                        "qty_EUR_20": bad,
                    }
                ),
                vessel.id,
                db=db,
                user=staff_user,
            )
        assert exc.value.status_code == 400
    assert await db.scalar(select(func.count()).select_from(CashCount)) == 0


@pytest.mark.asyncio
async def test_route_refuses_an_invalid_date(db, staff_user):
    from fastapi import HTTPException

    from app.routers import cashbox_router as r

    vessel, _cb = await _box(db)
    with pytest.raises(HTTPException) as exc:
        await r.submit_cash_count(
            _form_request(
                {
                    "trigger": "controle",
                    "counted_on": "31/08/2026",
                    "declared_by_name": "Cdt",
                    "declare_EUR": "1",
                    "qty_EUR_20": "1",
                }
            ),
            vessel.id,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400
