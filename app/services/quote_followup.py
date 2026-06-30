"""Relance J+1 sur devis non converti (nurturing avant-vente).

≠ suivi de règlement : c'est de la conversion **avant-vente**. Un e-mail léger
est envoyé J+1 si un devis invité avec email n'a pas donné de réservation. Une
seule relance par devis (`Quote.followup_sent_at`).

Déclenché par un cron externe (Power Automate) via
``POST /api/quotes/followup`` (X-API-Token). Best-effort : un échec SMTP
n'empêche pas de marquer les autres devis.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.booking import Booking
from app.models.leg import Leg
from app.models.port import Port
from app.models.quote import Quote
from app.services import email

logger = logging.getLogger("quote_followup")

# Fenêtre de relance : devis émis il y a 1 à 3 jours (tolère un cron quotidien
# qui aurait sauté un passage). Au-delà, on n'envoie plus (le devis « refroidit »).
FOLLOWUP_MIN_AGE_HOURS = 20
FOLLOWUP_MAX_AGE_DAYS = 3


async def _is_converted(db: AsyncSession, quote: Quote) -> bool:
    """Vrai si le devis a déjà donné une réservation."""
    if quote.status == "accepted":
        return True
    ref = (
        await db.execute(
            select(Booking.id).where(Booking.source_quote_reference == quote.reference).limit(1)
        )
    ).first()
    return ref is not None


async def find_pending(db: AsyncSession, *, now: datetime | None = None) -> list[Quote]:
    """Devis éligibles à la relance J+1 (email présent, non converti, non relancé)."""
    now = now or datetime.now(UTC)
    oldest = now - timedelta(days=FOLLOWUP_MAX_AGE_DAYS)
    newest = now - timedelta(hours=FOLLOWUP_MIN_AGE_HOURS)
    rows = (
        (
            await db.execute(
                select(Quote)
                .where(
                    Quote.contact_email.is_not(None),
                    Quote.followup_sent_at.is_(None),
                    Quote.status == "issued",
                    Quote.created_at >= oldest,
                    Quote.created_at <= newest,
                )
                .order_by(Quote.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    pending: list[Quote] = []
    for q in rows:
        if q.valid_until is not None and q.valid_until < now.date():
            continue
        if await _is_converted(db, q):
            continue
        pending.append(q)
    return pending


async def _port_name(db: AsyncSession, locode: str) -> str:
    port = (await db.execute(select(Port).where(Port.locode == locode))).scalar_one_or_none()
    return port.name if port and port.name else locode


async def send_followups(db: AsyncSession, *, now: datetime | None = None) -> dict:
    """Envoie les relances dues. Retourne un récapitulatif ``{candidates, sent}``."""
    now = now or datetime.now(UTC)
    pending = await find_pending(db, now=now)
    sent = 0
    for q in pending:
        pol_name = await _port_name(db, q.pol_locode)
        pod_name = await _port_name(db, q.pod_locode)
        # Lien de réservation pré-rempli : /booking/new/{leg}?quote={ref} si le
        # devis vise une traversée datée, sinon /booking/new.
        book_path = "/booking/new"
        if q.leg_id is not None:
            leg = await db.get(Leg, q.leg_id)
            if leg is not None:
                book_path = f"/booking/new/{leg.leg_code}?quote={q.reference}"
        ok = False
        try:
            ok = await email.send_template(
                "quote_followup",
                to=q.contact_email or "",
                recipient_name=q.contact_name or q.contact_company or q.contact_email,
                reference=q.reference,
                pol_name=pol_name,
                pod_name=pod_name,
                palettes=q.palettes_total,
                total_eur=q.total_eur,
                valid_until=q.valid_until.isoformat() if q.valid_until else "",
                book_url=f"{settings.site_url}{book_path}",
                quote_url=f"{settings.site_url}/devis/{q.reference}",
                site_url=settings.site_url,
                lang=q.lang or "fr",
            )
        except Exception:  # pragma: no cover - best-effort
            logger.warning("relance devis %s échouée", q.reference, exc_info=True)
        # On marque la relance même si SMTP est HS/no-op : une seule tentative
        # (évite le spam si le cron repasse ; SMTP non configuré = pas de boucle).
        q.followup_sent_at = now
        if ok:
            sent += 1
    await db.flush()
    return {"candidates": len(pending), "sent": sent}
