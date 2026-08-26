"""Cycle de vie d'une offre commerciale : échéance, validation, annulation.

Quatre états — ``en_cours``, ``valide``, ``echue``, ``annule`` — dont un seul est
calculé : **``echue``**. Une offre est échue quand sa date de validité est
dépassée **ou** que le navire est parti (ATD renseigné) : dans les deux cas la
proposition n'a plus d'objet, et laisser le volume réservé sur le leg fausserait
le chargement prévisionnel.

L'échéance est **matérialisée en base** par un balayage, pas seulement calculée à
l'affichage. Deux raisons : le volume réservé doit se libérer même si personne
n'ouvre l'écran, et l'historique doit porter la trace du passage à l'échéance.
Le calcul reste disponible (``is_expired``) pour ne jamais afficher « en cours »
une offre que le balayage n'a pas encore vue.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.commercial import RateOffer
from app.models.leg import Leg
from app.services.offer_history import record_revision, snapshot_offer


class OfferTransitionError(Exception):
    """Transition de statut refusée (offre déjà close)."""


def is_expired(offer: RateOffer, leg: Leg | None, *, on_date: date | None = None) -> bool:
    """L'offre est-elle échue ? Validité dépassée **ou** navire parti.

    Les deux conditions sont indépendantes : une offre encore dans sa fenêtre de
    validité mais dont le navire a appareillé est échue tout autant qu'une offre
    périmée sur un navire à quai.
    """
    today = on_date or datetime.now(UTC).date()
    if offer.valid_until is not None and offer.valid_until < today:
        return True
    return leg is not None and leg.atd is not None


async def expire_due_offers(db: AsyncSession, *, on_date: date | None = None) -> list[RateOffer]:
    """Passe en ``echue`` les offres en cours dont la validité ou le navire l'impose.

    Retourne les offres effectivement basculées. Idempotent : une offre déjà
    close n'est jamais retouchée.
    """
    today = on_date or datetime.now(UTC).date()
    stmt = (
        select(RateOffer, Leg)
        .outerjoin(Leg, Leg.id == RateOffer.leg_id)
        .where(
            RateOffer.status == "en_cours",
            or_(
                RateOffer.valid_until.is_not(None) & (RateOffer.valid_until < today),
                Leg.atd.is_not(None),
            ),
        )
    )
    expired: list[RateOffer] = []
    for offer, leg in (await db.execute(stmt)).all():
        if not is_expired(offer, leg, on_date=today):
            continue
        before = snapshot_offer(offer)
        offer.status = "echue"
        offer.expired_at = datetime.now(UTC)
        await db.flush()
        await record_revision(
            db,
            offer,
            action="expired",
            actor_name="système",
            before=before,
            comment=(
                "navire parti (ATD)"
                if leg is not None and leg.atd is not None
                else "date de validité dépassée"
            ),
        )
        expired.append(offer)
    return expired


async def validate_offer(
    db: AsyncSession,
    offer: RateOffer,
    *,
    actor_id: int | None = None,
    actor_name: str | None = None,
    actor_role: str | None = None,
) -> RateOffer:
    """Valide une offre en cours. Refuse toute offre déjà close.

    La validation est le point de bascule vers les opérations : c'est elle qui
    déclenche l'établissement de la booking note. Revalider une offre échue ou
    annulée reviendrait à ressusciter une proposition que le client ou le
    calendrier a déjà tranchée.
    """
    if not offer.is_open():
        raise OfferTransitionError(
            f"Offre {offer.reference} déjà close ({offer.status}) — elle ne peut plus être validée."
        )
    before = snapshot_offer(offer)
    offer.status = "valide"
    offer.validated_at = datetime.now(UTC)
    # ``accepted_at`` est conservé pour ne pas casser les lectures existantes.
    offer.accepted_at = offer.validated_at
    await db.flush()
    await record_revision(
        db,
        offer,
        action="validated",
        actor_id=actor_id,
        actor_name=actor_name,
        actor_role=actor_role,
        before=before,
    )
    return offer


async def cancel_offer(
    db: AsyncSession,
    offer: RateOffer,
    *,
    reason: str | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    actor_role: str | None = None,
) -> RateOffer:
    """Annule une offre sur décision du commercial (libère le volume réservé)."""
    if not offer.is_open():
        raise OfferTransitionError(
            f"Offre {offer.reference} déjà close ({offer.status}) — elle ne peut plus être annulée."
        )
    before = snapshot_offer(offer)
    offer.status = "annule"
    offer.cancelled_at = datetime.now(UTC)
    offer.cancelled_reason = (reason or "").strip() or None
    offer.declined_at = offer.cancelled_at
    await db.flush()
    await record_revision(
        db,
        offer,
        action="cancelled",
        actor_id=actor_id,
        actor_name=actor_name,
        actor_role=actor_role,
        before=before,
        comment=offer.cancelled_reason,
    )
    return offer


async def offers_reserving_leg(db: AsyncSession, leg_id: int) -> list[RateOffer]:
    """Offres réservant effectivement du volume sur un leg (pour l'affichage)."""
    from app.services.capacity import OFFER_RESERVED_STATUSES

    return list(
        (
            await db.execute(
                select(RateOffer)
                .options(selectinload(RateOffer.client))
                .where(
                    RateOffer.leg_id == leg_id,
                    RateOffer.status.in_(OFFER_RESERVED_STATUSES),
                )
                .order_by(RateOffer.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
