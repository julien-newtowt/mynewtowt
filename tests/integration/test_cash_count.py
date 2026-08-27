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
        counted_on=date(2026, 8, 31),
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
