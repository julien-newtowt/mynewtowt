"""Packing list — service métier (audit, lock, completion, token resolution).

Convention :
- Le token est stocké en clair dans `packing_lists.token` mais SHA-256
  côté `portal_access_logs.token_hash` (pas de fuite dans les logs).
- 90 jours de validité (`default_token_expiry`). Le router public renvoie
  410 GONE quand l'expiration est dépassée.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.commercial import Order
from app.models.leg import Leg
from app.models.packing_list import (
    PackingList,
    PackingListAudit,
    PackingListBatch,
    PortalAccessLog,
    default_token_expiry,
    generate_token,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.activity import record as activity_record

# CARGO-03 — champs de batch éditables soumis à l'audit field-by-field.
AUDITABLE_FIELDS: tuple[str, ...] = (
    "pallet_format",
    "pallet_count",
    "description",
    "hs_code",
    "weight_kg",
    "cubage_m3",
    "length_cm",
    "width_cm",
    "height_cm",
    "hazardous",
    "imdg_class",
    "un_number",
    "stackable",
    "marks_and_numbers",
    "shipper_name",
    "shipper_address",
    "shipper_postal",
    "shipper_city",
    "shipper_country",
    "notify_name",
    "notify_address",
    "notify_postal",
    "notify_city",
    "notify_country",
    "consignee_name",
    "consignee_address",
    "consignee_postal",
    "consignee_city",
    "consignee_country",
    "type_of_goods",
    "description_of_goods",
    "cases_quantity",
    "units_per_case",
    "cargo_value_usd",
)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_by_token(db: AsyncSession, token: str) -> PackingList | None:
    if not token:
        return None
    pl = (
        await db.execute(select(PackingList).where(PackingList.token == token))
    ).scalar_one_or_none()
    if pl is None:
        return None
    if pl.token_expires_at is not None and pl.token_expires_at < datetime.now(UTC):
        return None
    return pl


async def create_for_booking(db: AsyncSession, booking: Booking) -> PackingList:
    """Crée (ou retourne) la packing list d'un booking client — rail B.

    Jumeau de la création rail A (``cargo_packing_router.create_for_order``),
    mais rattaché à un ``booking_id`` au lieu d'un ``order_id``. Idempotent :
    si une PL existe déjà pour ce booking, on la retourne sans rien recréer
    (sûr à rappeler à chaque passage en ``confirmed``).

    Le client remplit ses batches via le portail ``/p/{token}`` — on ne crée
    donc que la coquille (token 24 hex / 90 j, statut ``draft``). On préremplit
    ce qui est cheap : ``loading_date`` = ETD du leg (alimente la cascade de
    dates). POL/POD/navire/référence vivent sur le leg et le booking, résolus
    à l'affichage côté portail — pas de colonnes dédiées sur PackingList.
    """
    existing = (
        await db.execute(select(PackingList).where(PackingList.booking_id == booking.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    leg = await db.get(Leg, booking.leg_id) if booking.leg_id else None

    pl = PackingList(
        booking_id=booking.id,
        order_id=None,
        leg_id=booking.leg_id,  # COM-11 — leg d'origine épinglé
        token=generate_token(),
        token_expires_at=default_token_expiry(),
        status="draft",
        loading_date=leg.etd if leg is not None else None,
    )
    db.add(pl)
    await db.flush()

    await activity_record(
        db,
        action="packing_list_created",
        module="cargo",
        entity_type="packing_list",
        entity_id=pl.id,
        entity_label=f"PL for {booking.reference}",
    )
    return pl


async def log_portal_access(
    db: AsyncSession,
    *,
    token: str,
    packing_list_id: int | None,
    ip_address: str | None,
    user_agent: str | None,
    path: str | None,
) -> None:
    db.add(
        PortalAccessLog(
            portal_type="cargo",
            token_hash=hash_token(token),
            packing_list_id=packing_list_id,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:400],
            path=(path or "")[:200],
        )
    )
    await db.flush()


async def record_audit(
    db: AsyncSession,
    *,
    packing_list_id: int,
    batch_id: int | None,
    actor: str,
    actor_name: str | None,
    field: str,
    old_value: str | None,
    new_value: str | None,
) -> None:
    if old_value == new_value:
        return
    db.add(
        PackingListAudit(
            packing_list_id=packing_list_id,
            batch_id=batch_id,
            actor=actor,
            actor_name=actor_name,
            field=field,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
        )
    )
    await db.flush()


# CARGO-03 — typage des champs de batch pour la coercition des formulaires
# (partagé entre la saisie staff et la saisie portail).
_BATCH_FLOAT_FIELDS = {
    "weight_kg",
    "cubage_m3",
    "length_cm",
    "width_cm",
    "height_cm",
    "cargo_value_usd",
}
_BATCH_INT_FIELDS = {"pallet_count", "cases_quantity", "units_per_case"}
_BATCH_BOOL_FIELDS = {"hazardous", "stackable"}


def coerce_batch_form(form: dict) -> dict:
    """Construit le dict de valeurs typées à partir d'un formulaire de batch.

    Seules les clés présentes dans le formulaire sont retournées (mise à jour
    partielle + audit ciblé). Chaînes vides → None.
    """
    out: dict = {}
    for field in AUDITABLE_FIELDS:
        if field not in form:
            continue
        raw = form.get(field)
        if field in _BATCH_BOOL_FIELDS:
            out[field] = str(raw).lower() in ("1", "true", "on", "yes")
            continue
        if raw is None or str(raw).strip() == "":
            out[field] = None
            continue
        if field in _BATCH_FLOAT_FIELDS:
            try:
                out[field] = float(str(raw).replace(",", "."))
            except ValueError:
                out[field] = None
        elif field in _BATCH_INT_FIELDS:
            try:
                out[field] = int(float(str(raw)))
            except ValueError:
                out[field] = None
        else:
            out[field] = str(raw).strip()
    return out


def can_modify(pl: PackingList) -> bool:
    return pl.status != "locked"


async def lock(db: AsyncSession, pl: PackingList, *, locked_by: str) -> PackingList:
    pl.status = "locked"
    pl.locked_at = datetime.now(UTC)
    pl.locked_by = locked_by
    await db.flush()
    return pl


async def unlock(db: AsyncSession, pl: PackingList) -> PackingList:
    pl.status = "submitted" if pl.batches else "draft"
    pl.locked_at = None
    pl.locked_by = None
    await db.flush()
    return pl


# CARGO-14 — empreinte standard (longueur × largeur, cm) par format de palette,
# pour pré-remplir les dimensions d'un batch laissées vides. Les formats sans
# empreinte standard connue (barriques) ne sont pas auto-remplis.
PALLET_DIMENSIONS: dict[str, tuple[float, float]] = {
    "EPAL": (120.0, 80.0),
    "USPAL": (120.0, 100.0),
    "PORTPAL": (120.0, 100.0),
    "IBC": (120.0, 100.0),
    "BIGBAG": (90.0, 90.0),
}


def apply_default_dimensions(vals: dict) -> dict:
    """Pré-remplit ``length_cm``/``width_cm`` depuis le format de palette quand
    elles sont absentes — sans jamais écraser une valeur saisie. Mute et renvoie
    ``vals``."""
    dims = PALLET_DIMENSIONS.get(vals.get("pallet_format") or "")
    if dims:
        if not vals.get("length_cm"):
            vals["length_cm"] = dims[0]
        if not vals.get("width_cm"):
            vals["width_cm"] = dims[1]
    return vals


async def create_batch(
    db: AsyncSession,
    *,
    pl: PackingList,
    vals: dict,
    actor: str,
    actor_name: str | None,
) -> PackingListBatch:
    """Crée un batch (numéro = rang suivant) + audit — partagé staff/portail.

    ``vals`` provient de ``coerce_batch_form`` ; les clés à None sont écartées
    par l'appelant pour laisser jouer les défauts des colonnes obligatoires.
    """
    count = int(
        (
            await db.scalar(
                select(func.count(PackingListBatch.id)).where(
                    PackingListBatch.packing_list_id == pl.id
                )
            )
        )
        or 0
    )
    b = PackingListBatch(
        packing_list_id=pl.id, batch_number=count + 1, **apply_default_dimensions(dict(vals))
    )
    db.add(b)
    await db.flush()
    await record_audit(
        db,
        packing_list_id=pl.id,
        batch_id=b.id,
        actor=actor,
        actor_name=actor_name,
        field="_create_batch",
        old_value=None,
        new_value=f"{b.pallet_count}×{b.pallet_format}",
    )
    return b


async def apply_batch_update(
    db: AsyncSession,
    *,
    batch: PackingListBatch,
    new_values: dict,
    actor: str,
    actor_name: str | None,
) -> int:
    """CARGO-03 — applique les changements d'un batch en traçant chaque champ.

    Seuls les champs de ``AUDITABLE_FIELDS`` présents dans ``new_values`` et
    réellement modifiés sont écrits (et audités). Retourne le nombre de champs
    modifiés.
    """
    changed = 0
    for field in AUDITABLE_FIELDS:
        if field not in new_values:
            continue
        old = getattr(batch, field)
        new = new_values[field]
        if old == new:
            continue
        setattr(batch, field, new)
        await record_audit(
            db,
            packing_list_id=batch.packing_list_id,
            batch_id=batch.id,
            actor=actor,
            actor_name=actor_name,
            field=field,
            old_value=old,
            new_value=new,
        )
        changed += 1
    if changed:
        await db.flush()
    return changed


async def resolve_pl_context(
    db: AsyncSession, pl: PackingList
) -> tuple[Order | None, Booking | None, Leg | None, Vessel | None, Port | None, Port | None]:
    """Résout (order, booking, leg, vessel, pol, pod) d'une packing list.

    Gère les deux rails : PL issue d'une commande (``order_id``) OU d'un
    booking (``booking_id``) — cf. CheckConstraint XOR sur PackingList.
    """
    order = await db.get(Order, pl.order_id) if pl.order_id else None
    booking = await db.get(Booking, pl.booking_id) if pl.booking_id else None
    # COM-11 — leg épinglé prioritaire (stable après réaffectation partielle) ;
    # repli dynamique sur order/booking pour les PL héritées (leg_id NULL).
    leg_id = pl.leg_id or (order.leg_id if order else None) or (booking.leg_id if booking else None)
    leg = await db.get(Leg, leg_id) if leg_id else None
    vessel = await db.get(Vessel, leg.vessel_id) if leg and leg.vessel_id else None
    pol = await db.get(Port, leg.departure_port_id) if leg and leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg and leg.arrival_port_id else None
    return order, booking, leg, vessel, pol, pod


async def _count_issued_bls_for_leg(db: AsyncSession, *, leg_id: int) -> int:
    """Nombre de BL déjà émis (toutes packing lists) sur un leg donné."""
    stmt = (
        select(func.count(PackingListBatch.id))
        .select_from(PackingListBatch)
        .join(PackingList, PackingListBatch.packing_list_id == PackingList.id)
        .outerjoin(Order, PackingList.order_id == Order.id)
        .outerjoin(Booking, PackingList.booking_id == Booking.id)
        .where(PackingListBatch.bl_number.is_not(None))
        # COM-11 — leg épinglé prioritaire, repli sur order/booking (PL héritées).
        .where(func.coalesce(PackingList.leg_id, Order.leg_id, Booking.leg_id) == leg_id)
    )
    return int((await db.scalar(stmt)) or 0)


async def assign_bl_number(
    db: AsyncSession, pl: PackingList, batch: PackingListBatch, leg: Leg | None
) -> str:
    """CARGO-01 — affecte (idempotent) un numéro de BL ``TUAW_{leg_code}_{seq:03d}``.

    Anti-doublon par leg : la séquence = nombre de BL déjà émis sur le leg + 1.
    Si le batch a déjà un numéro, on le renvoie sans rien changer.

    La colonne ``bl_number`` est UNIQUE ; en cas de collision concurrente
    (deux émissions simultanées sur le même leg lisant le même compteur), le
    flush échoue et on ré-essaie avec le compteur réactualisé (savepoint).
    """
    if batch.bl_number:
        return batch.bl_number
    voyage = leg.leg_code if (leg and leg.leg_code) else "NA"
    last_error: Exception | None = None
    for _attempt in range(5):
        seq = (await _count_issued_bls_for_leg(db, leg_id=leg.id) + 1) if leg else 1
        batch.bl_number = f"TUAW_{voyage}_{seq:03d}"
        batch.bl_issued_at = datetime.now(UTC)
        try:
            async with db.begin_nested():
                await db.flush()
            return batch.bl_number
        except IntegrityError as exc:  # collision de numéro → on recompte
            last_error = exc
            batch.bl_number = None
    raise last_error or RuntimeError("BL number assignment failed")


# ───────────────── COM-09 — packing list auto à la confirmation commande ──────


def batch_prefill_from_order(order: Order) -> dict:
    """CARGO-08 — valeurs d'un 1er batch pré-rempli depuis la commande.

    Reporte parties (shipper/consignee/notify), marchandise et volume connus
    de la commande. Les clés ``None`` sont écartées (défauts de colonne).
    """
    vals = {
        "pallet_count": order.booked_palettes or 1,
        "pallet_format": order.palette_format or "EPAL",
        "shipper_name": order.shipper_name,
        "shipper_address": order.shipper_address,
        "consignee_name": order.consignee_name,
        "consignee_address": order.consignee_address,
        "notify_name": order.notify_name,
        "notify_address": order.notify_address,
        "description_of_goods": order.description_of_goods or order.cargo_description,
    }
    return {k: v for k, v in vals.items() if v is not None}


async def ensure_for_order(db: AsyncSession, order: Order) -> tuple[PackingList, bool]:
    """Get-or-create la packing list d'une commande (idempotent).

    Retourne ``(packing_list, created)`` — ``created=False`` si une PL existait
    déjà pour la commande (pas de doublon à la re-confirmation). À la création,
    un 1er batch est **pré-rempli** depuis la commande (CARGO-08).
    """
    existing = (
        await db.execute(select(PackingList).where(PackingList.order_id == order.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    pl = PackingList(order_id=order.id, leg_id=order.leg_id, status="draft")
    db.add(pl)
    await db.flush()
    await create_batch(
        db,
        pl=pl,
        vals=batch_prefill_from_order(order),
        actor="system",
        actor_name="création commande",
    )
    return pl, True
