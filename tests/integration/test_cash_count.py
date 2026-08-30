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
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.cash_count import CashCount, CashCountLine
from app.models.onboard_cashbox import CashboxMovement
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
        counted_on=date(2026, 8, 20),
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
        counted_on=date(2026, 8, 20),
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
    # Motif « fin de mois » : il constate sans geler, contrairement à la relève
    # (cf. test_handover_freezes_the_departing_masters_accounting).
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
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
                "counted_on": "2026-08-20",
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
    assert count.counted_on == date(2026, 8, 20)
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
                        "counted_on": "2026-08-20",
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


# ── ADR-011 : espèces et encaissements carte séparés ────────────────────────


@pytest.mark.asyncio
async def test_card_settlements_stay_out_of_the_cash_balance(db, staff_user):
    """Le défaut que l'ADR-011 corrige : la variance était fausse chaque mois.

    Scénario du rapport d'audit — 400 € d'espèces, 1 200 € de CB, 300 € de
    décaissements. Le commandant compte 100 € de billets. Avant correction, le
    solde théorique valait 1 300 € et l'écart affiché −1 200 €, tous les mois,
    derrière lequel une perte d'espèces réelle devenait invisible.
    """
    _vessel, cb = await _box(db)
    await _movement(db, cb, "400.00", staff_user=staff_user)  # espèces
    await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal("1200.00"),
        currency="EUR",
        category="vente_a_bord",
        description="ventes réglées par carte",
        medium="card",
    )
    await _movement(db, cb, "-300.00", staff_user=staff_user)

    # Le solde théorique du comptage ne voit que les espèces.
    assert await svc.computed_balance(db, cb, "EUR") == Decimal("100.00")

    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("100"): 1}},  # 100 € comptés
    )
    assert count.currencies[0].variance == Decimal("0.00")


@pytest.mark.asyncio
async def test_a_real_cash_shortfall_is_now_visible(db, staff_user):
    """Corollaire : ce que l'écart « normal » masquait auparavant."""
    _vessel, cb = await _box(db)
    await _movement(db, cb, "400.00", staff_user=staff_user)
    await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal("1200.00"),
        currency="EUR",
        category="vente_a_bord",
        description="ventes CB",
        medium="card",
    )
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("100"): 2}},  # 200 € comptés au lieu de 400
    )
    assert count.currencies[0].variance == Decimal("-200.00")


@pytest.mark.asyncio
async def test_a_card_sale_never_credits_the_cash_box(db, staff_user):
    """Le support suit le moyen de paiement, jusque dans le mouvement créé."""
    from app.models.onboard_cashbox import CashboxMovement
    from app.models.onboard_sales import OnboardProduct
    from app.services import onboard_sales as sales_svc

    vessel, cb = await _box(db)
    product = OnboardProduct(
        sku="CAF-250", label="Café", kind="bien", unit_price=Decimal("6.50"), currency="EUR"
    )
    db.add(product)
    await db.flush()
    sale = await sales_svc.create_sale(db, vessel_id=vessel.id, currency="EUR")
    await sales_svc.add_line(db, sale, product=product, qty=Decimal("2"))
    await sales_svc.settle_sale(db, sale, payment_method="card")

    mov = await db.get(CashboxMovement, sale.cashbox_movement_id)
    assert mov.medium == "card"
    assert await svc.computed_balance(db, cb, "EUR") == Decimal("0.00")
    # Le mouvement reste au journal : le comptable en a besoin.
    assert (await cashbox_svc.card_totals(db, cb))["EUR"] == Decimal("13.00")


@pytest.mark.asyncio
async def test_cash_sale_credits_the_cash_box(db, staff_user):
    from app.models.onboard_sales import OnboardProduct
    from app.services import onboard_sales as sales_svc

    vessel, cb = await _box(db)
    product = OnboardProduct(
        sku="CAF-250", label="Café", kind="bien", unit_price=Decimal("6.50"), currency="EUR"
    )
    db.add(product)
    await db.flush()
    sale = await sales_svc.create_sale(db, vessel_id=vessel.id, currency="EUR")
    await sales_svc.add_line(db, sale, product=product, qty=Decimal("2"))
    await sales_svc.settle_sale(db, sale, payment_method="cash")
    assert await svc.computed_balance(db, cb, "EUR") == Decimal("13.00")
    assert await cashbox_svc.card_totals(db, cb) == {}


@pytest.mark.asyncio
async def test_monthly_closure_ignores_card_settlements(db, staff_user):
    _vessel, cb = await _box(db)
    when = datetime(2026, 8, 12, tzinfo=UTC)
    await _movement(db, cb, "500.00", when=when, staff_user=staff_user)
    await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal("900.00"),
        currency="EUR",
        category="vente_a_bord",
        description="ventes CB",
        medium="card",
        occurred_at=when,
    )
    closures = await cashbox_svc.close_month(
        db, cb, year=2026, month=8, counted={"EUR": Decimal("500.00")}
    )
    closure = next(c for c in closures if c.currency == "EUR")
    assert closure.computed_balance == Decimal("500.00")
    assert closure.variance == Decimal("0.00")
    # Les mouvements carte de la période sont tout de même verrouillés :
    # ils font partie du mois clôturé, ils sortent seulement du solde.
    movs = await cashbox_svc.period_movements(db, cb, year=2026, month=8)
    assert all(m.closure_id is not None for m in movs)


def test_export_csv_separates_media_and_totals():
    """Sans cette ventilation, le rapprochement bancaire confié au comptable
    par l'ADR-011 serait infaisable."""
    import types

    def _m(**kw):
        base = {
            "occurred_at": datetime(2026, 8, 3, tzinfo=UTC),
            "recorded_at": datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
            "currency": "EUR",
            "medium": "cash",
            "amount": Decimal("50.00"),
            "category": "vente_a_bord",
            "description": "vente",
            "receipt_url": None,
            "closure_id": None,
        }
        base.update(kw)
        return types.SimpleNamespace(**base)

    csv_text = cashbox_svc.export_csv(
        [_m(), _m(amount=Decimal("120.00"), medium="card")],
        vessel_code="ANE",
        period="2026-08",
    )
    assert "Support" in csv_text
    assert "Espèces" in csv_text and "Carte" in csv_text
    assert "Totaux par devise et support" in csv_text
    assert "EUR;Carte;120.00" in csv_text
    assert "EUR;Espèces;50.00" in csv_text


# ── ADR-013 : la relève fige la comptabilité du débarquant ──────────────────


@pytest.mark.asyncio
async def test_handover_freezes_the_departing_masters_accounting(db, staff_user):
    """Une relève est une décharge : la période remise passe en lecture seule."""
    from app.models.onboard_cashbox import CashboxMovement

    _vessel, cb = await _box(db)
    await _movement(db, cb, "100.00", when=datetime(2026, 8, 20, tzinfo=UTC), staff_user=staff_user)
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_embarquement",
        counted_on=date(2026, 8, 25),
        declared_by_name="Cdt Sortant",
        handover_to_name="Cdt Entrant",
        counts={"EUR": {Decimal("100"): 1}},
    )
    movs = (await db.execute(select(CashboxMovement))).scalars().all()
    assert all(m.locked_at is not None for m in movs)
    assert all(m.cash_count_id == count.id for m in movs)
    assert all(m.is_locked for m in movs)
    assert await cashbox_svc.frozen_until(db, cb) == date(2026, 8, 25)


@pytest.mark.asyncio
async def test_a_manual_entry_in_the_frozen_period_is_refused(db, staff_user):
    _vessel, cb = await _box(db)
    await svc.declare_count(
        db,
        cb,
        trigger="fin_embarquement",
        counted_on=date(2026, 8, 25),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("50"): 1}},
    )
    with pytest.raises(cashbox_svc.AccountingFrozen):
        await _movement(
            db, cb, "-30.00", when=datetime(2026, 8, 24, tzinfo=UTC), staff_user=staff_user
        )
    # Après la date de gel, l'écriture passe normalement.
    mov = await _movement(
        db, cb, "-30.00", when=datetime(2026, 8, 26, tzinfo=UTC), staff_user=staff_user
    )
    assert mov.id is not None


@pytest.mark.asyncio
async def test_a_settlement_is_deferred_rather_than_lost(db, staff_user):
    """Le point délicat : ne jamais perdre l'écriture d'un paiement encaissé.

    Un règlement confirmé après le gel mais daté dans la fenêtre gelée ne peut
    pas être refusé — l'argent a été reçu. Il est reporté au premier jour
    ouvert, avec mention explicite, plutôt que perdu.
    """
    _vessel, cb = await _box(db)
    await svc.declare_count(
        db,
        cb,
        trigger="fin_embarquement",
        counted_on=date(2026, 8, 25),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("50"): 1}},
    )
    mov = await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal("13.00"),
        currency="EUR",
        category="vente_a_bord",
        description="Vente à bord VB-2026-0007",
        occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
        defer_if_frozen=True,
    )
    assert mov.occurred_at.date() == date(2026, 8, 26)  # premier jour ouvert
    assert "reporté" in mov.description
    assert "2026-08-25" in mov.description


@pytest.mark.asyncio
async def test_a_card_payment_arriving_after_the_freeze_is_never_lost(db, staff_user):
    """Bout en bout : le webhook Stripe reste absorbable après une relève."""
    from app.models.onboard_sales import OnboardProduct
    from app.services import onboard_sales as sales_svc

    vessel, cb = await _box(db)
    product = OnboardProduct(
        sku="CAF-250", label="Café", kind="bien", unit_price=Decimal("6.50"), currency="EUR"
    )
    db.add(product)
    await db.flush()
    sale = await sales_svc.create_sale(db, vessel_id=vessel.id, currency="EUR")
    await sales_svc.add_line(db, sale, product=product, qty=Decimal("2"))

    # La relève est déclarée aujourd'hui : la journée courante est gelée.
    today = datetime.now(UTC).date()
    await svc.declare_count(
        db,
        cb,
        trigger="fin_embarquement",
        counted_on=today,
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("50"): 1}},
    )
    # Le paiement carte est confirmé juste après : il doit être encaissé.
    settled = await sales_svc.settle_sale(db, sale, payment_method="card")
    assert settled is True
    assert sale.status == "paid"


@pytest.mark.asyncio
async def test_a_monthly_close_does_not_freeze_future_entries(db, staff_user):
    """Seule la relève gèle ; « fin de mois » constate sans arrêter les comptes."""
    _vessel, cb = await _box(db)
    await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
        counted_on=date(2026, 8, 20),
        declared_by_name="Cdt",
        counts={"EUR": {Decimal("50"): 1}},
    )
    assert await cashbox_svc.frozen_until(db, cb) is None
    mov = await _movement(
        db, cb, "-10.00", when=datetime(2026, 8, 30, tzinfo=UTC), staff_user=staff_user
    )
    assert mov.id is not None


@pytest.mark.asyncio
async def test_balances_splits_income_and_expense(db, staff_user):
    """`balances()` n'avait aucune couverture : elle utilisait `greatest`/`least`,
    absents de SQLite, donc aucun test ne pouvait l'exécuter — alors qu'elle
    calcule le solde affiché à l'écran. Portée en SQL portable, on la verrouille."""
    _vessel, cb = await _box(db)
    await _movement(db, cb, "400.00", staff_user=staff_user)
    await _movement(db, cb, "-150.00", staff_user=staff_user)
    await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal("900.00"),
        currency="EUR",
        category="vente_a_bord",
        description="ventes CB",
        medium="card",
    )
    rows = await cashbox_svc.balances(db, cb)
    eur = next(r for r in rows if r.currency == "EUR")
    # Espèces seules (ADR-011).
    assert eur.balance == Decimal("250.00")
    assert eur.income_total == Decimal("400.00")
    assert eur.expense_total == Decimal("-150.00")
    assert eur.movement_count == 2

    # Tous supports confondus : total de contrôle.
    all_rows = await cashbox_svc.balances(db, cb, medium=None)
    assert next(r for r in all_rows if r.currency == "EUR").balance == Decimal("1150.00")


# ── Rectification d'un mouvement de caisse (dernier P0) ─────────────────────
#
# Le grand livre n'a ni UPDATE ni DELETE, et c'est délibéré : une écriture
# passée fait foi. Rectifier se fait donc comme en comptabilité — une
# contre-écriture datée du jour de la correction.


@pytest.mark.asyncio
async def test_a_correction_is_a_counter_entry_not_an_edit(db, staff_user):
    from app.models.onboard_cashbox import CashboxMovement

    _vessel, cb = await _box(db)
    wrong = await _movement(db, cb, "-500.00", staff_user=staff_user)  # doigt lourd
    original_amount = wrong.amount

    reversal, replacement = await cashbox_svc.reverse_movement(
        db,
        cb,
        wrong,
        reason="montant saisi à l'envers",
        corrected_amount=Decimal("-50.00"),
        recorded_by_id=staff_user.id,
    )
    # L'écriture d'origine est intacte.
    assert (await db.get(CashboxMovement, wrong.id)).amount == original_amount
    # La contre-écriture l'annule et le dit.
    assert reversal.amount == Decimal("500.00")
    assert reversal.reverses_movement_id == wrong.id
    assert "montant saisi à l'envers" in reversal.description
    assert replacement.amount == Decimal("-50.00")
    # Solde net : −50, comme si la saisie avait été correcte.
    assert await svc.computed_balance(db, cb, "EUR") == Decimal("-50.00")


@pytest.mark.asyncio
async def test_a_correction_can_simply_cancel(db, staff_user):
    _vessel, cb = await _box(db)
    wrong = await _movement(db, cb, "-80.00", staff_user=staff_user)
    reversal, replacement = await cashbox_svc.reverse_movement(
        db, cb, wrong, reason="dépense saisie deux fois"
    )
    assert replacement is None
    assert reversal.amount == Decimal("80.00")
    assert await svc.computed_balance(db, cb, "EUR") == Decimal("0.00")


@pytest.mark.asyncio
async def test_a_movement_is_only_corrected_once(db, staff_user):
    """Deux contre-écritures successives feraient dériver le solde."""
    _vessel, cb = await _box(db)
    wrong = await _movement(db, cb, "-80.00", staff_user=staff_user)
    await cashbox_svc.reverse_movement(db, cb, wrong, reason="erreur")
    with pytest.raises(cashbox_svc.CashboxError):
        await cashbox_svc.reverse_movement(db, cb, wrong, reason="encore")


@pytest.mark.asyncio
async def test_a_correction_requires_a_reason(db, staff_user):
    _vessel, cb = await _box(db)
    mov = await _movement(db, cb, "-10.00", staff_user=staff_user)
    with pytest.raises(cashbox_svc.CashboxError):
        await cashbox_svc.reverse_movement(db, cb, mov, reason="   ")


@pytest.mark.asyncio
async def test_a_movement_of_another_cashbox_is_refused(db, staff_user):
    _vessel_a, cb_a = await _box(db)
    vessel_b = Vessel(code="GRA", name="Grain de Sail")
    db.add(vessel_b)
    await db.flush()
    cb_b = await cashbox_svc.get_or_create(db, vessel_b.id)
    mov = await _movement(db, cb_a, "-10.00", staff_user=staff_user)
    with pytest.raises(cashbox_svc.CashboxError):
        await cashbox_svc.reverse_movement(db, cb_b, mov, reason="erreur")


@pytest.mark.asyncio
async def test_the_correction_keeps_the_medium_of_the_original(db, staff_user):
    """Rectifier un règlement carte ne doit pas sortir d'espèces du coffre."""
    _vessel, cb = await _box(db)
    card = await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal("120.00"),
        currency="EUR",
        category="vente_a_bord",
        description="vente CB",
        medium="card",
    )
    reversal, _ = await cashbox_svc.reverse_movement(db, cb, card, reason="vente annulée")
    assert reversal.medium == "card"
    # Le solde d'espèces n'a pas bougé.
    assert await svc.computed_balance(db, cb, "EUR") == Decimal("0.00")


@pytest.mark.asyncio
async def test_a_frozen_period_still_accepts_a_correction_dated_today(db, staff_user):
    """La contre-écriture est datée du jour : elle ne réécrit pas le passé gelé."""
    _vessel, cb = await _box(db)
    old = await _movement(
        db, cb, "-40.00", when=datetime(2026, 8, 20, tzinfo=UTC), staff_user=staff_user
    )
    await svc.declare_count(
        db,
        cb,
        trigger="fin_embarquement",
        counted_on=date(2026, 8, 25),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("5"): 1}},
    )
    reversal, _ = await cashbox_svc.reverse_movement(db, cb, old, reason="dépense non due")
    assert reversal.occurred_at.date() > date(2026, 8, 25)


@pytest.mark.asyncio
async def test_a_count_cannot_be_dated_in_the_future(db, staff_user):
    """Un gel daté de 2999 faisait disparaître l'argent encaissé des livres.

    Un « fin d'embarquement » daté dans le futur gelait la comptabilité pour
    toujours : plus aucune saisie manuelle possible, et chaque règlement
    reporté à une date hors de toute clôture — donc absent de tout export
    comptable, alors que l'argent avait bien été encaissé.
    """
    from datetime import timedelta

    _vessel, cb = await _box(db)
    demain = datetime.now(UTC).date() + timedelta(days=1)
    with pytest.raises(svc.CashCountError):
        await svc.declare_count(
            db,
            cb,
            trigger="fin_embarquement",
            counted_on=demain,
            declared_by_name="Cdt",
            counts={"EUR": {Decimal("5"): 1}},
        )
    # Rien n'a été gelé.
    assert await cashbox_svc.frozen_until(db, cb) is None
    assert await db.scalar(select(func.count()).select_from(CashCount)) == 0


# ── Pages du module caisse : ce que le bord voit réellement ─────────────────
#
# Retours du Cdt de l'ANEMOS le 2026-08-29 : la page caisse « a cassé toute la
# mise en page » dès qu'un état de caisse existait, et la grille de comptage
# n'affichait aucun total, obligeant à valider une déclaration définitive sans
# avoir pu vérifier ses chiffres. Les tests suivants rendent les pages réelles
# (layout compris) et vérifient ces deux points.


def _page_request(path: str):
    """Requête GET minimale, suffisante pour rendre une page staff."""
    from starlette.requests import Request

    request = Request(
        {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}
    )
    request.state.csrf_token = "test-csrf"
    return request


def _tag_balance(html: str, tag: str) -> tuple[int, int]:
    import re

    return (
        len(re.findall(rf"<{tag}\b", html)),
        len(re.findall(rf"</{tag}\s*>", html)),
    )


@pytest.mark.asyncio
async def test_the_cashbox_page_still_closes_its_layout_once_a_count_exists(db, staff_user):
    """Une fermeture ``</div>`` en trop refermait le ``<main>`` du layout.

    Le contenu suivant — nouveau mouvement, export, journal — sortait alors de
    la grille et s'affichait pleine largeur sous la barre latérale. Le défaut ne
    se déclenchait que dans la branche « au moins un état de caisse déclaré »,
    donc jamais sur une caisse neuve.
    """
    from app.routers import cashbox_router as r

    vessel, cb = await _box(db)
    await _movement(db, cb, "500.00", staff_user=staff_user)
    await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
        counted_on=date(2026, 8, 20),
        declared_by_name="Cdt Sortant",
        counts={"EUR": {Decimal("100"): 5}},
    )

    resp = await r.cashbox_detail(
        _page_request(f"/cashbox/{vessel.id}"), vessel.id, db=db, user=staff_user
    )
    html = resp.body.decode()

    assert "Contrôle de caisse" in html and "Cdt Sortant" in html
    opened, closed = _tag_balance(html, "div")
    assert opened == closed, f"<div> déséquilibrés : {opened} ouverts / {closed} fermés"
    # Le formulaire de mouvement doit rester **dans** le conteneur principal.
    assert html.index("Nouveau mouvement") < html.index("</main>")


@pytest.mark.asyncio
async def test_the_counting_grid_carries_the_live_total_wiring(db, staff_user):
    """La grille porte de quoi totaliser la déclaration pendant la saisie.

    Le commandant compte des coupures, pas des sommes : sans total vivant, il
    validait un état **définitif** sans avoir pu confronter son comptage au
    solde théorique. Le total affiché reste indicatif — le serveur le recalcule
    depuis les quantités — mais il doit exister.
    """
    from app.routers import cashbox_router as r

    vessel, cb = await _box(db)
    await _movement(db, cb, "1676.89", staff_user=staff_user)

    resp = await r.cash_count_form(
        _page_request(f"/cashbox/{vessel.id}/etat"), vessel.id, db=db, user=staff_user
    )
    html = resp.body.decode()

    # Un bloc calculable par devise, portant son solde théorique.
    for currency in ("EUR", "USD", "VND"):
        assert f'data-currency="{currency}"' in html
    assert 'data-computed="1676.89"' in html
    # Chaque coupure du référentiel est totalisable (valeur faciale portée).
    expected = sum(len(svc.denominations_for(c)) for c in ("EUR", "USD", "VND"))
    assert html.count("data-denom=") == expected
    assert "data-bulk" in html
    # Zones d'affichage lues par le script, et le script lui-même (CSP stricte :
    # fichier externe, jamais d'inline).
    for hook in ("data-cash-count-form", "data-counted-total", "data-variance", "data-declare"):
        assert hook in html, hook
    assert "js/cash-count-form.js" in html
    opened, closed = _tag_balance(html, "div")
    assert opened == closed


@pytest.mark.asyncio
async def test_the_declaration_is_confirmed_before_being_written(db, staff_user):
    """Une déclaration est définitive : elle ne part pas sur une fausse manœuvre.

    La confirmation est portée par ``forms.js`` (écouteur global
    ``form[data-confirm]``) ; ``cash-count-form.js`` n'en fournit que le
    récapitulatif, à jour à chaque frappe. Le formulaire doit donc être marqué
    pour cet écouteur, et la page annoncer le caractère irréversible.
    """
    from app.routers import cashbox_router as r

    vessel, _cb = await _box(db)
    resp = await r.cash_count_form(
        _page_request(f"/cashbox/{vessel.id}/etat"), vessel.id, db=db, user=staff_user
    )
    html = resp.body.decode()
    assert "data-cash-count-form" in html
    assert "définitif" in html
    assert "js/forms.js" in html, "la confirmation globale doit être chargée sur la page"


# ── Régularisation d'un écart — geste du siège, jamais du bord (ADR-014) ────
#
# Le contrôle de caisse constate un écart ; rien n'encadrait sa suite. Un
# commandant pouvait faire disparaître un manquant par un « Autre
# encaissement » indiscernable d'une écriture ordinaire : le contrôle
# constatait alors un écart que celui qui en répond pouvait solder lui-même.


async def _count_with_variance(db, staff_user, *, surplus="311.46", code="ANE"):
    """Reproduit le cas réel du 2026-08-29 : théorique 1 676,89 / compté 1 988,35."""
    vessel, cb = await _box(db, code=code)
    await _movement(db, cb, "1676.89", staff_user=staff_user)
    counted = Decimal("1676.89") + Decimal(surplus)
    # Une seule coupure fictive suffit : le bloc porte le total et l'écart.
    count = await svc.declare_count(
        db,
        cb,
        trigger="fin_de_mois",
        counted_on=datetime.now(UTC).date(),
        declared_by_name="LE GUIL G",
        counts={"EUR": {}},
        bulk_coins={"EUR": counted},
    )
    return vessel, cb, count


@pytest.mark.asyncio
async def test_the_bridge_cannot_reach_the_regularisation_categories(db, staff_user):
    """Les catégories de régularisation ne sont pas atteignables par le bord.

    Elles sont délibérément absentes des listes sélectionnables : la même
    exclusion ferme à la fois la liste déroulante et la validation de la route
    générique de mouvement — il n'y a pas de garde séparée à oublier.
    """
    from app.models.onboard_cashbox import (
        EXPENSE_CATEGORIES,
        INCOME_CATEGORIES,
        REGULARISATION_CATEGORIES,
        categories_for,
    )

    for code in REGULARISATION_CATEGORIES:
        assert code not in INCOME_CATEGORIES
        assert code not in EXPENSE_CATEGORIES
        assert code not in categories_for("income")
        assert code not in categories_for("expense")


@pytest.mark.asyncio
async def test_the_generic_movement_route_refuses_a_regularisation(db, staff_user):
    """Un POST direct du bord sur la route de mouvement est refusé."""
    from fastapi import HTTPException

    from app.routers import cashbox_router as r

    vessel, _cb = await _box(db)
    with pytest.raises(HTTPException) as exc:
        await r.add_mov(
            _page_request(f"/cashbox/{vessel.id}/movement"),
            vessel.id,
            amount="311.46",
            currency="EUR",
            category="regularisation_excedent",
            description="je solde mon écart moi-même",
            movement_kind="income",
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400
    assert await db.scalar(select(func.count()).select_from(CashboxMovement)) == 0


@pytest.mark.asyncio
async def test_a_surplus_is_settled_by_an_entry_of_the_right_sign(db, staff_user):
    """La régularisation suit le sens de l'écart, pas celui d'une saisie."""
    _vessel, cb, count = await _count_with_variance(db, staff_user)
    block = count.currencies[0]
    assert block.variance == Decimal("311.46")

    mov = await cashbox_svc.regularise_variance(
        db, cb, count, block, amount=Decimal("311.46"), reason="écart inexpliqué", recorded_by_id=1
    )
    assert mov.amount == Decimal("311.46")
    assert mov.category == "regularisation_excedent"
    assert mov.settles_cash_count_id == count.id
    # Le solde théorique rejoint la caisse réellement comptée.
    assert await svc.computed_balance(db, cb, "EUR") == Decimal("1988.35")


@pytest.mark.asyncio
async def test_a_shortfall_is_settled_by_an_outgoing_entry(db, staff_user):
    _vessel, cb, count = await _count_with_variance(db, staff_user, surplus="-120.00")
    block = count.currencies[0]
    assert block.variance == Decimal("-120.00")

    mov = await cashbox_svc.regularise_variance(
        db, cb, count, block, amount=Decimal("120.00"), reason="manquant constaté", recorded_by_id=1
    )
    assert mov.amount == Decimal("-120.00")
    assert mov.category == "regularisation_manquant"


@pytest.mark.asyncio
async def test_a_regularisation_never_exceeds_nor_flips_the_declared_variance(db, staff_user):
    """La borne est ce qui distingue cette écriture d'un « Autre encaissement ».

    Sans elle, la catégorie ne serait qu'un libellé plus flatteur posé sur un
    montant libre — aucun contrôle gagné.
    """
    _vessel, cb, count = await _count_with_variance(db, staff_user)
    block = count.currencies[0]

    with pytest.raises(cashbox_svc.RegularisationRefused):
        await cashbox_svc.regularise_variance(
            db, cb, count, block, amount=Decimal("400.00"), reason="trop"
        )
    with pytest.raises(cashbox_svc.RegularisationRefused):
        await cashbox_svc.regularise_variance(
            db, cb, count, block, amount=Decimal("-311.46"), reason="sens inversé"
        )
    with pytest.raises(cashbox_svc.RegularisationRefused):
        await cashbox_svc.regularise_variance(
            db, cb, count, block, amount=Decimal("50"), reason=" "
        )
    assert await db.scalar(select(func.count()).select_from(CashboxMovement)) == 1  # le dépôt seul


@pytest.mark.asyncio
async def test_the_same_variance_is_never_regularised_twice(db, staff_user):
    """L'écart étant figé, le restant se déduit des régularisations déjà passées.

    Sans cette soustraction, rejouer la régularisation doublerait la correction
    — et le solde théorique dépasserait la caisse réellement comptée.
    """
    _vessel, cb, count = await _count_with_variance(db, staff_user)
    block = count.currencies[0]

    await cashbox_svc.regularise_variance(
        db, cb, count, block, amount=Decimal("200.00"), reason="première part"
    )
    assert await cashbox_svc.remaining_variance(db, block) == Decimal("111.46")
    # L'écart déclaré, lui, n'a pas bougé : un contrôle rendu ne se réécrit pas.
    assert block.variance == Decimal("311.46")

    with pytest.raises(cashbox_svc.RegularisationRefused):
        await cashbox_svc.regularise_variance(
            db, cb, count, block, amount=Decimal("200.00"), reason="au-delà du restant"
        )
    await cashbox_svc.regularise_variance(
        db, cb, count, block, amount=Decimal("111.46"), reason="solde"
    )
    assert await cashbox_svc.remaining_variance(db, block) == Decimal("0.00")
    with pytest.raises(cashbox_svc.RegularisationRefused):
        await cashbox_svc.regularise_variance(
            db, cb, count, block, amount=Decimal("1.00"), reason="une fois de trop"
        )


@pytest.mark.asyncio
async def test_a_regularisation_is_dated_today_not_backdated_into_the_control(db, staff_user):
    """Antidater une régularisation réécrirait par la bande un contrôle rendu."""
    _vessel, cb, count = await _count_with_variance(db, staff_user)
    mov = await cashbox_svc.regularise_variance(
        db, cb, count, count.currencies[0], amount=Decimal("311.46"), reason="écart inexpliqué"
    )
    assert mov.occurred_at.date() == datetime.now(UTC).date()
    # La contrepartie est nommée : le contrôle, son motif et son déclarant.
    assert "LE GUIL G" in mov.description
    assert str(count.counted_on) in mov.description


@pytest.mark.asyncio
async def test_a_block_of_another_control_is_refused(db, staff_user):
    """Le bloc doit appartenir au contrôle visé, et le contrôle à cette caisse."""
    _vessel, cb, count = await _count_with_variance(db, staff_user)
    _other_vessel, other_cb, other_count = await _count_with_variance(
        db, staff_user, surplus="10", code="TUA"
    )

    with pytest.raises(cashbox_svc.RegularisationRefused):
        await cashbox_svc.regularise_variance(
            db, other_cb, count, count.currencies[0], amount=Decimal("10"), reason="mauvaise caisse"
        )
    with pytest.raises(cashbox_svc.RegularisationRefused):
        await cashbox_svc.regularise_variance(
            db, cb, count, other_count.currencies[0], amount=Decimal("10"), reason="mauvais bloc"
        )


@pytest.mark.asyncio
async def test_the_route_is_gated_on_the_office_permission(db, staff_user):
    """`finance:M`, jamais `ventes:M` — celle-là tient la caisse et la déclare."""
    import inspect

    from app.routers import cashbox_router as r

    dep = inspect.signature(r.regularise_count).parameters["user"].default
    # `require_permission("finance", "M")` est capturé dans la closure du Depends.
    closure = dep.dependency.__closure__ or ()
    captured = {c.cell_contents for c in closure if isinstance(c.cell_contents, str)}
    assert "finance" in captured and "M" in captured
    assert "ventes" not in captured


@pytest.mark.asyncio
async def test_the_control_page_offers_the_settlement_only_to_the_office(db, staff_user):
    """Le bord voit l'écart et la consigne ; il ne voit pas le formulaire."""
    from app.routers import cashbox_router as r

    vessel, _cb, count = await _count_with_variance(db, staff_user)

    seafarer = SimpleNamespace(
        id=2, username="cdt", role="marins", assigned_vessel_id=vessel.id, full_name="Cdt"
    )
    html_bord = (
        await r.cash_count_detail(
            _page_request(f"/cashbox/{vessel.id}/etat/{count.id}"),
            vessel.id,
            count.id,
            db=db,
            user=seafarer,
        )
    ).body.decode()
    assert "geste du siège" in html_bord
    assert "/regularisation" not in html_bord

    html_siege = (
        await r.cash_count_detail(
            _page_request(f"/cashbox/{vessel.id}/etat/{count.id}"),
            vessel.id,
            count.id,
            db=db,
            user=staff_user,  # administrateur → finance:M
        )
    ).body.decode()
    assert "Régulariser l'écart" in html_siege
    assert f"/cashbox/{vessel.id}/etat/{count.id}/regularisation" in html_siege


@pytest.mark.asyncio
async def test_rectifying_a_regularisation_reopens_the_variance(db, staff_user):
    """La contre-écriture d'une régularisation suit l'écart qu'elle soldait.

    Sans propagation de ``settles_cash_count_id``, rectifier une régularisation
    mal saisie laissait l'écart affiché comme **entièrement régularisé** alors
    que l'écriture venait d'être annulée — et fermait toute nouvelle
    régularisation sur ce contrôle.
    """
    _vessel, cb, count = await _count_with_variance(db, staff_user)
    block = count.currencies[0]

    mov = await cashbox_svc.regularise_variance(
        db, cb, count, block, amount=Decimal("311.46"), reason="montant erroné"
    )
    assert await cashbox_svc.remaining_variance(db, block) == Decimal("0.00")

    reversal, replacement = await cashbox_svc.reverse_movement(
        db, cb, mov, reason="montant saisi à l'envers", corrected_amount=Decimal("11.46")
    )
    assert reversal.settles_cash_count_id == count.id
    assert replacement is not None and replacement.settles_cash_count_id == count.id
    # 311,46 − 311,46 + 11,46 régularisés ⇒ il reste 300,00 à traiter.
    assert await cashbox_svc.remaining_variance(db, block) == Decimal("300.00")
    # …et une régularisation du restant redevient possible.
    await cashbox_svc.regularise_variance(
        db, cb, count, block, amount=Decimal("300.00"), reason="solde"
    )
    assert await cashbox_svc.remaining_variance(db, block) == Decimal("0.00")
