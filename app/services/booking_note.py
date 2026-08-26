"""Établissement de la booking note à la validation d'une offre commerciale.

La validation d'une offre est le point de bascule vers les opérations : le
logiciel établit alors automatiquement la booking note, préremplie depuis
l'offre, sa grille, le client et le voyage. Le commercial la relit, corrige ce
qui doit l'être, puis la diffuse.

Deux partis pris :

* **Préremplir, pas décider.** Chaque champ dérivé est calculé ici une fois, puis
  stocké : le commercial reste libre de le corriger, et ce qui a été diffusé
  reste consultable tel quel même si l'offre ou le référentiel évoluent ensuite.
* **Ne jamais inventer.** Un champ dont la donnée manque (agent au port de
  déchargement, adresse du client) reste **vide** plutôt que de recevoir un
  texte plausible : un contrat qui affirme une information fausse est pire
  qu'un contrat visiblement à compléter.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.booking_note import BookingNote
from app.models.commercial import Client, RateGrid, RateOffer
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel

# Lieu d'établissement par défaut — siège NEWTOWT (cf. gabarit : « LE HAVRE, … »).
DEFAULT_ISSUE_PLACE = "LE HAVRE"


class BookingNoteError(Exception):
    """Établissement ou diffusion de booking note refusé."""


def _port_text(port: Port | None) -> str | None:
    """« FRLEH – Le Havre, FR » — vide si le port n'est pas connu."""
    if port is None:
        return None
    bits = [port.locode, "–", port.name]
    text = " ".join(b for b in bits if b)
    return f"{text}, {port.country}" if port.country else text


def _merchant_address(client: Client | None) -> str | None:
    if client is None:
        return None
    return (client.address or "").strip() or None


def _freight_terms(offer: RateOffer, grid: RateGrid | None) -> str:
    """Conditions tarifaires complètes, en clair sur le contrat.

    Le client doit pouvoir vérifier le prix sans détenir la grille : on rappelle
    donc le tarif unitaire, le volume, le total, et les frais annexes qui
    s'appliqueront (options actives de la grille).
    """
    lines: list[str] = []
    palettes = offer.estimated_palettes or 0
    if offer.proposed_rate_eur is not None:
        lines.append(
            f"Fret : {offer.proposed_rate_eur} EUR par palette × {palettes} palette(s)"
        )
    else:
        lines.append(f"Fret : {palettes} palette(s)")
    if offer.total_eur is not None:
        lines.append(f"Total fret : {offer.total_eur} EUR")

    if grid is not None:
        lines.append(f"Grille tarifaire de référence : {grid.reference}")
        if grid.bl_fee:
            lines.append(f"Frais de connaissement (B/L) : {grid.bl_fee} EUR")
        if grid.booking_fee:
            lines.append(f"Frais de réservation : {grid.booking_fee} EUR")
        for option in grid.options:
            if option.is_active:
                from app.models.commercial import RATE_OPTION_UNIT_LABELS

                unit = RATE_OPTION_UNIT_LABELS.get(option.unit, option.unit)
                lines.append(f"{option.label} : {option.amount_eur} EUR {unit}")
        if grid.min_charge_eur:
            lines.append(f"Minimum de facturation : {grid.min_charge_eur} EUR")
        if grid.hazardous_surcharge_pct:
            lines.append(
                f"Surcharge marchandises dangereuses : {grid.hazardous_surcharge_pct} %"
            )
    return "\n".join(lines)


def _payment_terms(grid: RateGrid | None) -> str:
    """Échéancier contractuel, repris des conditions de règlement de la grille."""
    if grid is None or not grid.payment_terms:
        return ""
    return "\n".join(
        f"{term.percentage} % — {term.trigger_label}"
        + (f" ({term.label})" if term.label else "")
        for term in grid.payment_terms
    )


async def next_booking_note_reference(db: AsyncSession) -> str:
    """Référence ``BN-AAAA-NNNN``, séquentielle par année.

    Comptage sur le préfixe de l'année : les booking notes ne sont pas
    supprimées (une fois diffusées elles sont gelées), la séquence ne peut donc
    pas régresser — contrairement au piège rencontré sur les numéros de
    connaissement, où la suppression d'un lot recyclait un numéro déjà émis.
    """
    year = datetime.now(UTC).year
    from sqlalchemy import func

    count = (
        await db.scalar(
            select(func.count(BookingNote.id)).where(
                BookingNote.reference.like(f"BN-{year}-%")
            )
        )
    ) or 0
    return f"BN-{year}-{int(count) + 1:04d}"


async def ensure_for_offer(db: AsyncSession, offer: RateOffer) -> BookingNote:
    """Établit (ou retourne) la booking note d'une offre validée.

    **Idempotent** : revalider une offre ne fabrique pas un second contrat. Si
    une booking note existe déjà, elle est retournée telle quelle — y compris
    ses corrections manuelles, qu'un réétablissement écraserait.
    """
    if offer.status != "valide":
        raise BookingNoteError(
            "La booking note n'est établie qu'à la validation de l'offre "
            f"(offre {offer.reference} : {offer.status})."
        )

    existing = (
        await db.execute(select(BookingNote).where(BookingNote.offer_id == offer.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    client = await db.get(Client, offer.client_id)
    leg = await db.get(Leg, offer.leg_id) if offer.leg_id else None
    vessel = await db.get(Vessel, leg.vessel_id) if leg is not None else None
    pol = await db.get(Port, leg.departure_port_id) if leg is not None else None
    pod = await db.get(Port, leg.arrival_port_id) if leg is not None else None
    grid = (
        (
            await db.execute(
                select(RateGrid)
                .options(selectinload(RateGrid.options), selectinload(RateGrid.payment_terms))
                .where(RateGrid.id == offer.grid_id)
            )
        ).scalar_one_or_none()
        if offer.grid_id
        else None
    )

    note = BookingNote(
        offer_id=offer.id,
        reference=await next_booking_note_reference(db),
        status="brouillon",
        issue_place=DEFAULT_ISSUE_PLACE,
        issued_on=datetime.now(UTC),
        vessel_name=vessel.name if vessel is not None else None,
        # « Time for shipment (about) » = ETD du voyage (arbitrage 2026-08-26).
        time_for_shipment=(
            leg.etd.strftime("%d/%m/%Y") if leg is not None and leg.etd else None
        ),
        pol_text=_port_text(pol),
        pod_text=_port_text(pod),
        merchant_name=client.name if client is not None else None,
        merchant_contact=client.contact_name if client is not None else None,
        merchant_address=_merchant_address(client),
        merchant_email=client.contact_email if client is not None else None,
        freight_terms=_freight_terms(offer, grid),
        payment_terms=_payment_terms(grid),
        # ``agents_pod`` reste vide : l'agent au port de déchargement n'est pas
        # une donnée du système, il est désigné escale par escale. Le commercial
        # le renseigne — mieux vaut un champ à compléter qu'un nom inventé.
        agents_pod=None,
    )
    db.add(note)
    await db.flush()
    return note


async def mark_issued(
    db: AsyncSession,
    note: BookingNote,
    *,
    document: bytes | None = None,
    user_id: int | None = None,
    user_name: str | None = None,
) -> BookingNote:
    """Passe la booking note en « diffusée » et la gèle.

    L'empreinte du document effectivement diffusé est conservée : elle permet de
    démontrer que le fichier détenu par le client est bien celui qui a été émis.
    """
    if note.status == "diffusee":
        raise BookingNoteError(f"Booking note {note.reference} déjà diffusée.")
    note.status = "diffusee"
    note.issued_at = datetime.now(UTC)
    note.issued_by_id = user_id
    note.issued_by_name = user_name
    if document is not None:
        note.document_sha256 = hashlib.sha256(document).hexdigest()
    await db.flush()
    return note


def total_with_options(offer: RateOffer, grid: RateGrid | None) -> Decimal | None:
    """Total fret + frais documentaires fixes de la grille (indicatif)."""
    if offer.total_eur is None:
        return None
    total = Decimal(offer.total_eur)
    if grid is not None:
        total += Decimal(grid.bl_fee or 0) + Decimal(grid.booking_fee or 0)
    return total
