"""Onboard cashbox routes — one cashbox per vessel, multi-currency.

Encaissement (income) / décaissement (expense) avec catégories distinctes,
pièces justificatives (scan), export comptable et clôture mensuelle qui
verrouille les mouvements de la période.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cash_count import (
    CASH_COUNT_TRIGGER_LABELS,
    CASH_COUNT_TRIGGERS,
    CashCount,
)
from app.models.onboard_cashbox import (
    CATEGORY_KIND,
    CATEGORY_LABELS,
    CURRENCY_LABELS,
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    SUPPORTED_CURRENCIES,
    CashboxMovement,
    categories_for,
)
from app.models.vessel import Vessel
from app.permissions import (
    SEAFARER_ROLES,
    VesselAccessDenied,
    assert_vessel_access,
    has_permission,
    require_permission,
    visible_vessel_id,
)
from app.services import cash_count as cash_count_svc
from app.services import safe_files
from app.services.activity import record as activity_record
from app.services.cashbox import (
    AccountingFrozen,
    CashboxError,
    PeriodClosed,
    RegularisationRefused,
    add_movement,
    as_movement_date,
    balances,
    card_totals,
    close_month,
    export_csv,
    frozen_until,
    get_or_create,
    list_closures,
    period_movements,
    recent_movements,
    regularise_variance,
    regularised_by_count,
    remaining_variance,
    reverse_movement,
)
from app.templating import templates
from app.utils.decimals import CENTS, DecimalInputError, parse_decimal

router = APIRouter(prefix="/cashbox", tags=["cashbox"])

_RECEIPT_SUBDIR = "cashbox/receipts"
_EXPORT_SUBDIR = "cashbox/exports"


def _check_vessel(user, vessel_id: int | None) -> None:
    """Cloisonnement par navire (ADR-012) — 403 explicite en cas de refus."""
    try:
        assert_vessel_access(user, vessel_id)
    except VesselAccessDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None


@router.get("", response_class=HTMLResponse)
async def cashbox_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "C")),
) -> HTMLResponse:
    # Un utilisateur borné ne voit que sa caisse : le solde et les mouvements
    # d'un autre navire ne font pas partie des consultations restées ouvertes
    # sur la flotte (ADR-012 — seuls planning et positions le sont).
    scoped = visible_vessel_id(user)
    if scoped is None and getattr(user, "role", None) in SEAFARER_ROLES:
        _check_vessel(user, None)  # marin sans affectation → refus explicite
    stmt = select(Vessel).order_by(Vessel.code)
    if scoped is not None:
        stmt = stmt.where(Vessel.id == scoped)
    vessels = list((await db.execute(stmt)).scalars().all())
    summary = []
    for v in vessels:
        cb = await get_or_create(db, v.id)
        bal = await balances(db, cb)
        summary.append({"vessel": v, "cashbox": cb, "balances": bal})
    return templates.TemplateResponse(
        "staff/cashbox/index.html",
        {
            "request": request,
            "user": user,
            "summary": summary,
            "currency_labels": CURRENCY_LABELS,
        },
    )


@router.get("/{vessel_id}", response_class=HTMLResponse)
async def cashbox_detail(
    request: Request,
    vessel_id: int,
    currency: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "C")),
) -> HTMLResponse:
    _check_vessel(user, vessel_id)
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)
    bal = await balances(db, cb)  # espèces (ADR-011)
    cards = await card_totals(db, cb)
    frozen = await frozen_until(db, cb)
    mvts = await recent_movements(db, cb, currency=currency, limit=200)
    closures = await list_closures(db, cb, limit=24)
    now = datetime.now(UTC)
    return templates.TemplateResponse(
        "staff/cashbox/detail.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "cashbox": cb,
            "balances": bal,
            "card_totals": cards,
            "frozen_until": frozen,
            "movements": mvts,
            "closures": closures,
            "currency_filter": currency,
            "currencies": SUPPORTED_CURRENCIES,
            "currency_labels": CURRENCY_LABELS,
            "income_categories": INCOME_CATEGORIES,
            "expense_categories": EXPENSE_CATEGORIES,
            "category_labels": CATEGORY_LABELS,
            "category_kind": CATEGORY_KIND,
            "default_year": now.year,
            "default_month": now.month,
            "cash_counts": await cash_count_svc.history(db, cb, limit=12),
        },
    )


# ── Contrôle de caisse : état déclaré par le commandant ─────────────────────


@router.get("/{vessel_id}/etat", response_class=HTMLResponse)
async def cash_count_form(
    request: Request,
    vessel_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "M")),
) -> HTMLResponse:
    """Grille de comptage — une colonne par devise, une ligne par coupure."""
    _check_vessel(user, vessel_id)
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)
    today = datetime.now(UTC).date()
    blocks = []
    for cur in SUPPORTED_CURRENCIES:
        blocks.append(
            {
                "currency": cur,
                "denominations": cash_count_svc.denominations_for(cur),
                # Solde théorique affiché à titre indicatif : le commandant
                # compte d'abord, l'écart se lit ensuite. On ne le met pas dans
                # un champ pré-rempli — un comptage qu'on aligne sur le
                # théorique ne contrôle rien.
                "computed": await cash_count_svc.computed_balance(db, cb, cur, upto=today),
            }
        )
    return templates.TemplateResponse(
        "staff/cashbox/cash_count_form.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "cashbox": cb,
            "blocks": blocks,
            "today": today,
            "triggers": CASH_COUNT_TRIGGERS,
            "trigger_labels": CASH_COUNT_TRIGGER_LABELS,
            "currency_labels": CURRENCY_LABELS,
        },
    )


@router.post("/{vessel_id}/etat")
async def submit_cash_count(
    request: Request,
    vessel_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "M")),
) -> RedirectResponse:
    """Enregistre l'état déclaré et fige les écarts par devise.

    Les quantités arrivent sous la forme ``qty_{DEVISE}_{valeur}`` ; le total
    n'est jamais repris du formulaire, il est recalculé depuis ces quantités.
    """
    _check_vessel(user, vessel_id)
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)
    form = await request.form()

    raw_date = str(form.get("counted_on") or "").strip()
    try:
        counted_on = (
            datetime.fromisoformat(raw_date).date() if raw_date else datetime.now(UTC).date()
        )
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Date de comptage invalide (format attendu AAAA-MM-JJ)."
        ) from None

    counts: dict[str, dict[Decimal, int]] = {}
    bulk_coins: dict[str, Decimal] = {}
    reasons: dict[str, str] = {}
    for cur in SUPPORTED_CURRENCIES:
        # Une devise n'est déclarée que si le commandant l'a cochée : un bloc à
        # zéro pour une devise réellement détenue produirait un faux écart.
        if not form.get(f"declare_{cur}"):
            continue
        quantities: dict[Decimal, int] = {}
        for value, _kind in cash_count_svc.denominations_for(cur):
            raw = str(form.get(f"qty_{cur}_{value}") or "").strip()
            if not raw:
                continue
            try:
                qty = int(raw)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Nombre de coupures invalide ({cur} {value}).",
                ) from None
            if qty < 0:
                raise HTTPException(
                    status_code=400, detail=f"Nombre de coupures négatif ({cur} {value})."
                )
            if qty:
                quantities[value] = qty
        raw_bulk = str(form.get(f"bulk_{cur}") or "").strip()
        if raw_bulk:
            try:
                bulk_coins[cur] = parse_decimal(
                    raw_bulk, label=f"pièces en vrac {cur}", min_value=Decimal("0"), quantize=CENTS
                )
            except DecimalInputError as e:
                raise HTTPException(status_code=400, detail=str(e)) from None
        reason = str(form.get(f"reason_{cur}") or "").strip()
        if reason:
            reasons[cur] = reason
        counts[cur] = quantities

    declared_by = (
        str(form.get("declared_by_name") or "").strip()
        or getattr(user, "full_name", None)
        or user.username
    )

    try:
        count = await cash_count_svc.declare_count(
            db,
            cb,
            trigger=str(form.get("trigger") or "controle"),
            counted_on=counted_on,
            declared_by_name=declared_by,
            declared_by_id=user.id,
            handover_to_name=str(form.get("handover_to_name") or "").strip() or None,
            counts=counts,
            bulk_coins=bulk_coins,
            variance_reasons=reasons,
            notes=str(form.get("notes") or "").strip() or None,
        )
    except cash_count_svc.CashCountError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await activity_record(
        db,
        action="cashbox_cash_count",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="cash_count",
        entity_id=count.id,
        detail=(
            f"vessel={vessel_id} {count.trigger} {count.counted_on} — écarts : "
            + ", ".join(f"{c.currency} {c.variance}" for c in count.currencies)
        ),
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}/etat/{count.id}", status_code=303)


@router.get("/{vessel_id}/etat/{count_id}", response_class=HTMLResponse)
async def cash_count_detail(
    request: Request,
    vessel_id: int,
    count_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "C")),
) -> HTMLResponse:
    _check_vessel(user, vessel_id)
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    count = await db.get(CashCount, count_id)
    if count is None:
        raise HTTPException(status_code=404, detail="État de caisse introuvable")
    cb = await get_or_create(db, vessel_id)
    if count.cashbox_id != cb.id:
        raise HTTPException(status_code=404, detail="État de caisse introuvable")
    # Suite donnée à l'écart : ce qui a déjà été régularisé, et ce qui reste.
    # L'écart lui-même n'est jamais recalculé — il est figé (c'est l'objet du
    # contrôle) ; le restant se déduit des régularisations rattachées.
    settlement = {
        block.currency: {
            "movements": await regularised_by_count(db, count.id, block.currency),
            "remaining": await remaining_variance(db, block),
        }
        for block in count.currencies
    }
    return templates.TemplateResponse(
        "staff/cashbox/cash_count_detail.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "count": count,
            "currency_labels": CURRENCY_LABELS,
            "settlement": settlement,
            # Affichage seulement : la route de régularisation refait le
            # contrôle sur la matrice effective (overrides compris).
            "can_regularise": has_permission(getattr(user, "role", ""), "finance", "M"),
        },
    )


@router.post("/{vessel_id}/etat/{count_id}/regularisation")
async def regularise_count(
    vessel_id: int,
    count_id: int,
    currency: str = Form(...),
    amount: str = Form(...),
    reason: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "M")),
) -> RedirectResponse:
    """Solde tout ou partie d'un écart constaté — **réservé au siège** (ADR-014).

    La permission est délibérément `finance:M`, jamais `ventes:M` : cette
    dernière est celle qui tient la caisse et qui déclare le comptage. Un rôle
    capable de constater un écart *et* de le faire disparaître ne se contrôle
    pas lui-même — c'est le même raisonnement que pour le remboursement
    (ADR-013). Le bord signale ; le siège régularise.

    Pas de cloisonnement par navire ici : le siège régularise pour toute la
    flotte, comme il rembourse pour toute la flotte.
    """
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)
    count = await db.get(CashCount, count_id)
    if count is None or count.cashbox_id != cb.id:
        raise HTTPException(status_code=404, detail="État de caisse introuvable")
    block = next((b for b in count.currencies if b.currency == currency.upper()), None)
    if block is None:
        raise HTTPException(
            status_code=400, detail=f"Ce contrôle ne déclare pas de bloc {currency.upper()}."
        )
    try:
        amt = parse_decimal(amount, label="montant", quantize=CENTS)
    except DecimalInputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    try:
        movement = await regularise_variance(
            db, cb, count, block, amount=amt, reason=reason, recorded_by_id=user.id
        )
    except (RegularisationRefused, AccountingFrozen, PeriodClosed) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CashboxError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await activity_record(
        db,
        action="cashbox_variance_regularised",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="finance",
        entity_type="cashbox_movement",
        entity_id=movement.id,
        detail=(
            f"vessel={vessel_id} contrôle #{count.id} ({count.counted_on}) "
            f"écart {block.currency} {block.variance} — régularisé de "
            f"{movement.amount} par #{movement.id} : {reason.strip()}"
        ),
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}/etat/{count_id}", status_code=303)


@router.post("/{vessel_id}/movement")
async def add_mov(
    request: Request,
    vessel_id: int,
    amount: str = Form(...),
    currency: str = Form(...),
    category: str = Form(...),
    description: str = Form(...),
    movement_kind: str = Form("expense"),  # "income" | "expense"
    occurred_at: str = Form(""),
    receipt: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "M")),
) -> RedirectResponse:
    _check_vessel(user, vessel_id)
    cb = await get_or_create(db, vessel_id)
    if movement_kind not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="Invalid movement kind")
    if category not in categories_for(movement_kind):
        raise HTTPException(
            status_code=400, detail="Catégorie incompatible avec le sens du mouvement"
        )
    try:
        amt = abs(parse_decimal(amount, label="montant", quantize=CENTS))
    except DecimalInputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if movement_kind == "expense":
        amt = -amt
    # Date d'effet : une saisie illisible était silencieusement remplacée par
    # « maintenant » — le mouvement antidaté par le commandant atterrissait au
    # jour courant, sans le moindre signal. On refuse plutôt que de deviner.
    occ = None
    if occurred_at.strip():
        try:
            occ = datetime.fromisoformat(occurred_at.replace("T", " "))
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date d'effet invalide (format attendu AAAA-MM-JJ HH:MM)."
            ) from None
        if occ.tzinfo is None:
            occ = occ.replace(tzinfo=UTC)
        # Le formulaire ne saisit plus qu'une date ; on tronque quand même, pour
        # les requêtes qui porteraient encore une heure (page mise en cache).
        occ = as_movement_date(occ)

    receipt_url, receipt_mime = await _maybe_store_receipt(receipt)

    try:
        mov = await add_movement(
            db,
            cb,
            amount=amt,
            currency=currency,
            category=category,
            description=description,
            occurred_at=occ,
            recorded_by_id=user.id,
            receipt_url=receipt_url,
            receipt_mime=receipt_mime,
        )
    except PeriodClosed as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CashboxError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await activity_record(
        db,
        action="cashbox_movement",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="cashbox_movement",
        entity_id=mov.id,
        detail=f"vessel={vessel_id} {amt} {currency} {category}",
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}", status_code=303)


@router.post("/{vessel_id}/movement/{mov_id}/receipt")
async def attach_receipt(
    vessel_id: int,
    mov_id: int,
    receipt: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "M")),
) -> RedirectResponse:
    _check_vessel(user, vessel_id)
    mov = await _get_movement(db, vessel_id, mov_id)
    if mov.is_locked:
        raise HTTPException(status_code=400, detail="Mouvement verrouillé (clôturé)")
    rel, mime = await _maybe_store_receipt(receipt)
    if rel is None:
        raise HTTPException(status_code=400, detail="Aucun fichier valide")
    mov.receipt_url = rel
    mov.receipt_mime = mime
    await db.flush()
    await activity_record(
        db,
        action="cashbox_receipt",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="cashbox_movement",
        entity_id=mov.id,
        detail=f"vessel={vessel_id} justificatif",
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}", status_code=303)


@router.get("/{vessel_id}/movement/{mov_id}/receipt")
async def view_receipt(
    vessel_id: int,
    mov_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "C")),
) -> Response:
    _check_vessel(user, vessel_id)
    mov = await _get_movement(db, vessel_id, mov_id)
    if not mov.receipt_url:
        raise HTTPException(status_code=404, detail="Pas de justificatif")
    try:
        path = safe_files.resolve_path(mov.receipt_url)
    except (safe_files.UploadRejected, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail="Fichier introuvable") from e
    return Response(
        content=path.read_bytes(),
        media_type=mov.receipt_mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="justificatif-{mov_id}{path.suffix}"'},
    )


@router.post("/{vessel_id}/movement/{mov_id}/correct")
async def correct_movement(
    vessel_id: int,
    mov_id: int,
    reason: str = Form(...),
    corrected_amount: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "M")),
) -> RedirectResponse:
    """Rectifie un mouvement mal saisi, par contre-écriture.

    Le grand livre n'a ni route de modification ni route de suppression, et
    c'est délibéré. Rectifier se fait comme en comptabilité : un mouvement
    opposé, daté du jour de la correction, puis le montant correct s'il y a
    lieu. La période d'origine n'est jamais réécrite — un contrôle de caisse
    déjà rendu reste ce qu'il était.
    """
    _check_vessel(user, vessel_id)
    cb = await get_or_create(db, vessel_id)
    mov = await _get_movement(db, vessel_id, mov_id)

    amount = None
    if corrected_amount.strip():
        try:
            amount = parse_decimal(corrected_amount, label="montant corrigé", quantize=CENTS)
        except DecimalInputError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        # Le sens du mouvement d'origine fait foi : une dépense reste une
        # dépense, on n'en fait pas un encaissement par une faute de signe.
        amount = -abs(amount) if mov.amount < 0 else abs(amount)

    try:
        reversal, replacement = await reverse_movement(
            db,
            cb,
            mov,
            reason=reason,
            corrected_amount=amount,
            recorded_by_id=user.id,
        )
    except PeriodClosed as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except AccountingFrozen as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CashboxError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await activity_record(
        db,
        action="cashbox_movement_corrected",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="cashbox_movement",
        entity_id=mov.id,
        detail=(
            f"vessel={vessel_id} #{mov.id} ({mov.amount} {mov.currency}) "
            f"annulé par #{reversal.id}"
            + (f", remplacé par #{replacement.id} ({replacement.amount})" if replacement else "")
            + f" — {reason.strip()}"
        ),
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}", status_code=303)


@router.get("/{vessel_id}/export.csv")
async def export_period(
    vessel_id: int,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "C")),
) -> Response:
    _check_vessel(user, vessel_id)
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)
    movs = await period_movements(db, cb, year=year, month=month)
    csv_text = export_csv(movs, vessel_code=vessel.code, period=f"{year}-{month:02d}")
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="caisse-{vessel.code}-{year}{month:02d}.csv"'
            )
        },
    )


@router.post("/{vessel_id}/close")
async def close_period(
    request: Request,
    vessel_id: int,
    year: int = Form(..., ge=2000, le=2100),
    month: int = Form(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("ventes", "M")),
) -> RedirectResponse:
    _check_vessel(user, vessel_id)
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)

    # Soldes comptés saisis par devise : champ counted_<CUR>.
    form = await request.form()
    counted: dict[str, Decimal] = {}
    for cur in SUPPORTED_CURRENCIES:
        raw = str(form.get(f"counted_{cur}") or "").strip()
        if not raw:
            continue
        try:
            counted[cur] = parse_decimal(raw, label=f"solde compté {cur}", quantize=CENTS)
        except DecimalInputError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    # Export comptable d'abord (les données sont exportées puis verrouillées).
    movs = await period_movements(db, cb, year=year, month=month)
    csv_text = export_csv(movs, vessel_code=vessel.code, period=f"{year}-{month:02d}")
    export_path = None
    try:
        export_path, _mime = safe_files.save_upload(
            csv_text.encode("utf-8"),
            f"caisse-{vessel.code}-{year}{month:02d}.csv",
            subdir=_EXPORT_SUBDIR,
        )
    except safe_files.UploadRejected:
        export_path = None  # export best-effort ; la clôture verrouille quoi qu'il arrive

    try:
        closures = await close_month(
            db,
            cb,
            year=year,
            month=month,
            counted=counted,
            closed_by_id=user.id,
            export_path=export_path,
        )
    except CashboxError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await activity_record(
        db,
        action="cashbox_close",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="cashbox_closure",
        entity_id=closures[0].id if closures else None,
        detail=f"vessel={vessel_id} {year}-{month:02d} devises={[c.currency for c in closures]}",
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_movement(db: AsyncSession, vessel_id: int, mov_id: int) -> CashboxMovement:
    cb = await get_or_create(db, vessel_id)
    mov = await db.get(CashboxMovement, mov_id)
    if mov is None or mov.cashbox_id != cb.id:
        raise HTTPException(status_code=404, detail="Mouvement introuvable")
    return mov


async def _maybe_store_receipt(
    receipt: UploadFile | None,
) -> tuple[str | None, str | None]:
    """Valide + enregistre un justificatif uploadé. (None, None) si absent."""
    if receipt is None or not receipt.filename:
        return None, None
    content = await receipt.read()
    if not content:
        return None, None
    try:
        rel, mime = safe_files.save_upload(content, receipt.filename, subdir=_RECEIPT_SUBDIR)
    except safe_files.UploadRejected as e:
        raise HTTPException(status_code=400, detail=f"Justificatif rejeté : {e}") from e
    return rel, mime
