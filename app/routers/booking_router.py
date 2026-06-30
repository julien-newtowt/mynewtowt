"""Booking wizard for clients — 3-step flow, session invité + autocréation.

Steps:
1. /booking/new                  - choose a leg (form & search)
2. /booking/new/{leg_code}       - cargo details (+ IMDG/FDS si dangereux)
3. /booking/{ref}/confirm        - review + coordonnées (autocréation) + CGV
   → submit() crée le compte client (si invité) PUIS passe la réservation en
     status=submitted de façon atomique, connecte l'utilisateur, et redirige
     vers /booking/{ref}/done.

Le wizard tourne en **session invité** : les étapes 1-2 n'exigent plus de
compte. Le brouillon de réservation est créé en étape 2 (client_account_id
nullable) ; l'identité est collectée en étape 3 et le compte est créé à la
validation. L'équipe commerciale confirme sous 4h et émet la booking note par
virement bancaire (cf. pdf/invoice.html). Pas de paiement en ligne.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    CLIENT_COOKIE,
    AuthError,
    cookie_kwargs_for_client,
    create_client_session,
    get_current_client,
)
from app.config import settings
from app.database import get_db
from app.models.leg import Leg
from app.models.packing_list import PackingListDocument
from app.models.port import Port
from app.models.vessel import Vessel
from app.services import analytics
from app.services import client_account as client_account_service
from app.services.activity import record as activity_record
from app.services.booking import (
    BookingError,
    BookingItemInput,
    create_draft,
    submit,
)
from app.services.capacity import (
    BookingClosed,
    CapacityExceeded,
    NotBookable,
    get_available_capacity,
)
from app.services.imdg import IMDG_CLASSES, is_valid_imdg_code, is_valid_un_number
from app.services.safe_files import UploadRejected, save_upload
from app.templating import templates

router = APIRouter(prefix="/booking", tags=["booking"])

# Nombre maximal de lignes palettes balayées dans le formulaire cargo.
_MAX_ITEM_ROWS = 30

# Cookie signé d'appropriation du brouillon invité : seul le navigateur qui a
# créé le brouillon peut le consulter / le valider (le compte n'existe pas
# encore). Contient la référence du booking, signée (itsdangerous).
_DRAFT_COOKIE = "towt_booking_draft"
_DRAFT_MAX_AGE = 7200  # 2 h pour finaliser le wizard
_draft_serializer = URLSafeTimedSerializer(settings.secret_key, salt="booking-draft")


async def optional_client(
    session_cookie: Annotated[str | None, Cookie(alias=CLIENT_COOKIE)] = None,
    db: AsyncSession = Depends(get_db),
):
    """Client authentifié si présent, sinon None — jamais d'exception."""
    if not session_cookie:
        return None
    try:
        return await get_current_client(session_cookie=session_cookie, db=db)
    except AuthError:
        return None


def _sign_draft(ref: str) -> str:
    return _draft_serializer.dumps(ref)


def _owns_draft(request: Request, ref: str) -> bool:
    raw = request.cookies.get(_DRAFT_COOKIE)
    if not raw:
        return False
    try:
        value = _draft_serializer.loads(raw, max_age=_DRAFT_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return value == ref


@router.get("/new", response_class=HTMLResponse)
async def step_1_search(
    request: Request,
    client=Depends(optional_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    # Show available legs (next 90 days, bookable)
    res = await db.execute(
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.is_bookable.is_(True))
        .order_by(Leg.etd.asc())
        .limit(20)
    )

    # Le suivi du remplissage n'est plus exposé (FLX-01) : la capacité sert
    # uniquement à filtrer les traversées fermées ou complètes, sans publier
    # de chiffres. Le prix est restitué par l'outil de devis (grilles).
    items = []
    for leg, vessel in res.all():
        try:
            capacity = await get_available_capacity(db, leg.id)
        except (NotBookable, BookingClosed):
            continue
        if capacity.available_palettes <= 0:
            continue
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        items.append({"leg": leg, "vessel": vessel, "pol": pol, "pod": pod})
    return templates.TemplateResponse(
        "client/booking_step1.html",
        {"request": request, "client": client, "legs": items},
    )


@router.get("/new/{leg_code}", response_class=HTMLResponse)
async def step_2_cargo_form(
    request: Request,
    leg_code: str,
    quote: str | None = None,
    client=Depends(optional_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    leg = await _get_bookable_leg(db, leg_code)
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)
    distance_nm = 0.0
    if pol and pod and pol.latitude is not None and pod.latitude is not None:
        from app.services.ports import haversine_nm

        distance_nm = round(
            haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude),
            1,
        )

    # Conversion devis → réservation : pré-remplissage depuis un devis
    # (référence en query ?quote= ou cookie towt_pending_quote).
    quote_ref = quote or request.cookies.get("towt_pending_quote")
    prefill = await _quote_prefill(db, leg, quote_ref)

    # Analytics tunnel : clic « Réserver cette traversée » (entrée wizard cargo).
    await analytics.record(
        db,
        "book_click",
        reference=leg.leg_code,
        lang=getattr(request.state, "lang", "fr"),
        channel="client" if client else "public",
    )

    return templates.TemplateResponse(
        "client/booking_step2.html",
        {
            "request": request,
            "client": client,
            "leg": leg,
            "pol": pol,
            "pod": pod,
            "distance_nm": distance_nm,
            "prefill": prefill,
            "imdg_classes": IMDG_CLASSES,
        },
    )


async def _quote_prefill(db: AsyncSession, leg: Leg, quote_ref: str | None) -> dict | None:
    """Pré-remplissage (format/quantité de la 1ʳᵉ ligne + réf devis) si le
    devis correspond bien à la traversée. Best-effort, jamais bloquant."""
    if not quote_ref:
        return None
    import contextlib

    with contextlib.suppress(Exception):
        from app.services.quoting import find_quote

        q = await find_quote(db, quote_ref)
        if q is None or (q.leg_id is not None and q.leg_id != leg.id):
            return None
        raw = json.loads(q.items_json) if q.items_json else []
        items = [
            {"format": f, "count": c}
            for f, c in raw[:_MAX_ITEM_ROWS]
            if isinstance(c, int) and c > 0
        ]
        if not items:
            return None
        return {"reference": q.reference, "items": items}
    return None


def _parse_cargo_items(form) -> list[BookingItemInput]:
    """Reconstruit les lignes cargo depuis le formulaire (tolère les trous).

    Si une ligne est marquée dangereuse, on lui applique les champs IMDG
    partagés du formulaire (classe / n° ONU / code SH) — cf. collecte F4.
    """
    shared_imdg_class = (form.get("imdg_class") or "").strip() or None
    shared_un_number = (form.get("un_number") or "").strip() or None
    shared_hs_code = (form.get("hs_code") or "").strip() or None

    items_raw: list[BookingItemInput] = []
    for i in range(_MAX_ITEM_ROWS):
        fmt = form.get(f"items-{i}-format")
        if fmt is None:
            continue
        try:
            count = int(form.get(f"items-{i}-count", "0"))
            description = (form.get(f"items-{i}-description") or "").strip()
            unit_weight = form.get(f"items-{i}-unit_weight_kg")
            hazardous = form.get(f"items-{i}-hazardous") == "on"
            stackable = form.get(f"items-{i}-stackable") != "off"
            if count > 0 and description:
                items_raw.append(
                    BookingItemInput(
                        pallet_format=str(fmt),
                        pallet_count=count,
                        cargo_description=description,
                        unit_weight_kg=Decimal(unit_weight) if unit_weight else None,
                        hazardous=hazardous,
                        stackable=stackable,
                        imdg_class=shared_imdg_class if hazardous else None,
                        un_number=shared_un_number if hazardous else None,
                        hs_code=shared_hs_code if hazardous else None,
                    )
                )
        except (ValueError, TypeError):
            pass
    return items_raw


@router.post("/new/{leg_code}", response_class=HTMLResponse)
async def step_2_cargo_submit(
    request: Request,
    leg_code: str,
    client=Depends(optional_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    leg = await _get_bookable_leg(db, leg_code)
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)
    distance_nm = 0.0
    if pol and pod and pol.latitude is not None and pod.latitude is not None:
        from app.services.ports import haversine_nm

        distance_nm = round(
            haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude), 1
        )
    form = await request.form()
    items_raw = _parse_cargo_items(form)

    def _error(message: str, code: int = 400) -> HTMLResponse:
        return templates.TemplateResponse(
            "client/booking_step2.html",
            {
                "request": request,
                "client": client,
                "leg": leg,
                "pol": pol,
                "pod": pod,
                "distance_nm": distance_nm,
                "imdg_classes": IMDG_CLASSES,
                # Conserve la saisie IMDG pour ne pas la perdre au re-rendu.
                "imdg_form": {
                    "imdg_class": (form.get("imdg_class") or "").strip(),
                    "un_number": (form.get("un_number") or "").strip(),
                    "hs_code": (form.get("hs_code") or "").strip(),
                },
                "error": message,
            },
            status_code=code,
        )

    if not items_raw:
        return _error("Veuillez ajouter au moins une palette à la réservation.")

    # IMDG / FDS (F4) : si au moins une ligne est dangereuse, la fiche de
    # données de sécurité (FDS) est obligatoire et la classe IMDG requise.
    has_hazardous = any(i.hazardous for i in items_raw)
    fds_upload = form.get("fds_file")
    fds_present = bool(getattr(fds_upload, "filename", "") or "")
    if has_hazardous:
        imdg_class = (form.get("imdg_class") or "").strip()
        if not imdg_class:
            return _error("Indiquez la classe IMDG pour les marchandises dangereuses.")
        if not is_valid_imdg_code(imdg_class):
            return _error("Classe IMDG invalide. Sélectionnez une classe dans la liste.")
        un_number = (form.get("un_number") or "").strip()
        if un_number and not is_valid_un_number(un_number):
            return _error("Numéro ONU invalide (format attendu : UN1203).")
        if not fds_present:
            return _error(
                "La fiche de données de sécurité (FDS) est obligatoire pour les marchandises dangereuses."
            )

    quote_ref = (form.get("quote") or "").strip() or request.cookies.get("towt_pending_quote")
    if quote_ref:
        quote_ref = quote_ref[:24]

    try:
        booking, _quote = await create_draft(
            db,
            client=client,
            leg=leg,
            items=items_raw,
            pickup_address=(form.get("pickup_address") or None),
            delivery_address=(form.get("delivery_address") or None),
            shipper_reference=(form.get("shipper_reference") or None),
            notes=(form.get("notes") or None),
            source_quote_reference=quote_ref,
        )
    except CapacityExceeded as e:
        return _error(f"Capacité insuffisante: {e}")
    except BookingError as e:
        return _error(str(e))

    # Pièce jointe FDS (best-effort sur la validation du fichier).
    if has_hazardous and fds_present and isinstance(fds_upload, UploadFile):
        try:
            content = await fds_upload.read()
            rel_path, mime = save_upload(
                content, fds_upload.filename or "fds", subdir="booking-fds"
            )
            db.add(
                PackingListDocument(
                    booking_id=booking.id,
                    kind="msds",
                    label=(fds_upload.filename or "FDS")[:200],
                    file_path=rel_path,
                    file_mime=mime,
                    uploaded_by="client",
                )
            )
            await db.flush()
        except UploadRejected as e:
            return _error(f"Fichier FDS rejeté : {e}")

    resp = RedirectResponse(url=f"/booking/{booking.reference}/confirm", status_code=303)
    # Invité : on signe l'appropriation du brouillon dans un cookie.
    if client is None:
        resp.set_cookie(
            _DRAFT_COOKIE,
            _sign_draft(booking.reference),
            max_age=_DRAFT_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
    return resp


@router.get("/{ref}/confirm", response_class=HTMLResponse)
async def step_3_confirm_form(
    request: Request,
    ref: str,
    client=Depends(optional_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = await _get_wizard_draft(db, request, ref, client)
    # Si l'utilisateur s'est connecté entre-temps (cas email existant), on
    # rattache le brouillon invité à son compte.
    if client is not None and booking.client_account_id is None:
        booking.client_account_id = client.id
        await db.flush()
    leg = await db.get(Leg, booking.leg_id)
    return templates.TemplateResponse(
        "client/booking_step3.html",
        {
            "request": request,
            "client": client,
            "booking": booking,
            "leg": leg,
            "needs_account": client is None,
        },
    )


@router.post("/{ref}/confirm", response_class=HTMLResponse)
async def step_3_confirm_submit(
    request: Request,
    ref: str,
    client=Depends(optional_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = await _get_wizard_draft(db, request, ref, client)
    # Idempotence : un double-clic (booking déjà soumis) ne doit pas relancer la
    # transition — on renvoie simplement vers la page de confirmation.
    if booking.status == "submitted":
        return RedirectResponse(url=f"/booking/{booking.reference}/done", status_code=303)
    leg = await db.get(Leg, booking.leg_id)
    form = await request.form()
    accept_terms = form.get("accept_terms")

    def _render(extra: dict, code: int = 400) -> HTMLResponse:
        ctx = {
            "request": request,
            "client": client,
            "booking": booking,
            "leg": leg,
            "needs_account": client is None,
            "values": dict(form),
        }
        ctx.update(extra)
        return templates.TemplateResponse("client/booking_step3.html", ctx, status_code=code)

    def _error(message: str, code: int = 400) -> HTMLResponse:
        return _render({"error": message}, code)

    if accept_terms != "on":
        return _error("Vous devez accepter les CGV pour confirmer.")

    account_created = False
    session_token: str | None = None

    if client is None:
        # Autocréation du compte à la validation (cœur de la décision).
        email = (form.get("email") or "").strip()
        login_url = f"/me/login?next=/booking/{booking.reference}/confirm"
        existing = await client_account_service.find_by_email(db, email)
        if existing is not None:
            return _render({"account_exists": True, "login_url": login_url})
        try:
            account = await client_account_service.create_account(
                db,
                email=email,
                password=(form.get("password") or ""),
                company_name=(form.get("company_name") or ""),
                contact_name=(form.get("contact_name") or None),
                country=(form.get("country") or None),
                language=getattr(request.state, "lang", "fr") or "fr",
            )
        except client_account_service.EmailAlreadyExists:
            return _render({"account_exists": True, "login_url": login_url})
        except client_account_service.AccountError as exc:
            return _error(str(exc))

        booking.client_account_id = account.id
        await db.flush()
        account_created = True
        session_token = create_client_session(account.id)
        await activity_record(
            db,
            action="client_register",
            user_name=account.email,
            module="booking",
            entity_type="client_account",
            entity_id=account.id,
            entity_label=account.company_name,
            ip_address=_client_ip(request),
        )
        await analytics.record(
            db,
            "account_created",
            reference=booking.reference,
            lang=getattr(request.state, "lang", "fr"),
            channel="client",
        )
        client = account
    elif booking.client_account_id is None:
        booking.client_account_id = client.id
        await db.flush()

    booking.signed_terms_version = "v2026.1"
    booking.signed_terms_at = datetime.now(UTC)
    await submit(db, booking)

    from app.services.booking_lifecycle import on_status_change

    await on_status_change(db, booking, "submitted")

    # Marque le devis source comme converti (neutralise la relance J+1).
    if booking.source_quote_reference:
        await _mark_quote_converted(db, booking.source_quote_reference)

    await activity_record(
        db,
        action="booking_submit",
        user_name=client.email,
        module="booking",
        entity_type="booking",
        entity_id=booking.id,
        entity_label=booking.reference,
        ip_address=_client_ip(request),
    )
    await analytics.record(
        db,
        "booking_submitted",
        reference=booking.reference,
        lang=getattr(request.state, "lang", "fr"),
        channel="client",
        detail="account_created" if account_created else "existing_account",
    )

    resp = RedirectResponse(url=f"/booking/{booking.reference}/done", status_code=303)
    resp.delete_cookie(_DRAFT_COOKIE, path="/")
    if session_token:
        resp.set_cookie(value=session_token, **cookie_kwargs_for_client(request))
    return resp


@router.get("/{ref}/done", response_class=HTMLResponse)
async def step_4_done(
    request: Request,
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = await _get_my_booking(db, ref, client.id)
    return templates.TemplateResponse(
        "client/booking_done.html",
        {"request": request, "client": client, "booking": booking},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mark_quote_converted(db: AsyncSession, reference: str) -> None:
    import contextlib

    with contextlib.suppress(Exception):
        from app.services.quoting import find_quote

        q = await find_quote(db, reference)
        if q is not None and q.status == "issued":
            q.status = "accepted"
            await db.flush()


async def _get_bookable_leg(db: AsyncSession, leg_code: str) -> Leg:
    leg = (await db.execute(select(Leg).where(Leg.leg_code == leg_code))).scalar_one_or_none()
    if not leg or not leg.is_bookable:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leg not found")
    return leg


async def _get_my_booking(db: AsyncSession, ref: str, client_id: int):
    from app.services.booking import find_by_reference

    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return booking


async def _get_wizard_draft(db: AsyncSession, request: Request, ref: str, client):
    """Charge un brouillon du wizard en vérifiant l'appropriation.

    Autorisé si : (a) le brouillon appartient au client connecté, ou (b) le
    brouillon est encore invité (client_account_id NULL) et le cookie signé
    d'appropriation correspond. Statut requis : draft/submitted.
    """
    from app.services.booking import find_by_reference

    booking = await find_by_reference(db, ref)
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    owned_by_client = client is not None and booking.client_account_id == client.id
    owned_as_guest = booking.client_account_id is None and _owns_draft(request, ref)
    if not (owned_by_client or owned_as_guest):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if booking.status not in ("draft", "submitted"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Booking in status {booking.status} cannot be modified",
        )
    return booking


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
