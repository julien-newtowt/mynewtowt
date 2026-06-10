"""Label Anemos — résolution de distance + émission du certificat.

L'émission est **idempotente** : un seul certificat par booking. Elle est
déclenchée par le cycle de vie du booking à ``discharged``/``delivered``
(cf. ``services/booking_lifecycle.py``) et peut aussi être appelée à la
demande.

La distance est résolue dans cet ordre :
1. ``leg.distance_nm`` (persistée — source de vérité après 1ʳᵉ traversée) ;
2. haversine depuis les coordonnées POL/POD (et on persiste le résultat
   sur le leg pour les fois suivantes) ;
3. table de paires de ports en dur (fallback historique V3.0).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.leg import Leg
from app.models.port import Port
from app.services.activity import record as activity_record
from app.services.co2 import estimate as estimate_co2
from app.services.ports import haversine_nm


# Fallback historique (V3.0) — paires de ports connues, distance orthodromique.
_DISTANCE_NM: dict[frozenset[str], Decimal] = {
    frozenset({"FRFEC", "USNYC"}): Decimal("3200"),
    frozenset({"FRLEH", "USNYC"}): Decimal("3180"),
    frozenset({"FRFEC", "USBOS"}): Decimal("3020"),
    frozenset({"FRLEH", "USBOS"}): Decimal("3050"),
    frozenset({"FRLEH", "BRSSO"}): Decimal("4900"),
    frozenset({"FRFEC", "BRSSO"}): Decimal("4920"),
    frozenset({"FRLEH", "PTPDL"}): Decimal("1450"),
    frozenset({"FRFEC", "PTPDL"}): Decimal("1480"),
    frozenset({"PTPDL", "USNYC"}): Decimal("2280"),
    frozenset({"PTPDL", "USBOS"}): Decimal("2150"),
}

_DEFAULT_DISTANCE_NM = Decimal("3000")


def _table_distance(pol_locode: str | None, pod_locode: str | None) -> Decimal:
    if pol_locode and pod_locode:
        return _DISTANCE_NM.get(frozenset({pol_locode, pod_locode}), _DEFAULT_DISTANCE_NM)
    return _DEFAULT_DISTANCE_NM


def resolve_distance_nm(leg: Leg | None, pol: Port | None, pod: Port | None) -> Decimal:
    """Distance NM pour un leg, selon l'ordre persistée → haversine → table."""
    if leg is not None and leg.distance_nm is not None:
        return Decimal(leg.distance_nm)
    if (
        pol is not None and pod is not None
        and pol.latitude is not None and pol.longitude is not None
        and pod.latitude is not None and pod.longitude is not None
    ):
        nm = haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude)
        return Decimal(str(round(nm, 2)))
    return _table_distance(
        getattr(pol, "locode", None), getattr(pod, "locode", None)
    )


async def issue_for_booking(db: AsyncSession, booking: Booking) -> AnemosCertificate:
    """Crée (ou retourne) le label Anemos d'un booking. Idempotent."""
    existing = (
        await db.execute(
            select(AnemosCertificate).where(AnemosCertificate.booking_id == booking.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    leg = await db.get(Leg, booking.leg_id)
    pol = await db.get(Port, leg.departure_port_id) if leg else None
    pod = await db.get(Port, leg.arrival_port_id) if leg else None

    distance = resolve_distance_nm(leg, pol, pod)
    # Persiste la distance sur le leg si elle n'y était pas (store-after-crossing).
    if leg is not None and leg.distance_nm is None:
        leg.distance_nm = distance

    tonnage = (booking.total_weight_kg or Decimal("0")) / Decimal("1000")
    emission = estimate_co2(distance_nm=distance, tonnage_t=tonnage)

    cert = AnemosCertificate(
        reference=f"ANEMOS-{booking.reference}",
        booking_id=booking.id,
        client_account_id=booking.client_account_id,
        leg_id=booking.leg_id,
        tonnage_transported_t=tonnage,
        distance_nm=distance,
        co2_emitted_kg=emission.towt_co2_kg,
        co2_conventional_kg=emission.conventional_co2_kg,
        co2_avoided_kg=emission.avoided_co2_kg,
    )
    db.add(cert)
    await db.flush()

    await activity_record(
        db,
        action="anemos_issued",
        user_name="system",
        module="kpi",
        entity_type="anemos_certificate",
        entity_id=cert.id,
        entity_label=cert.reference,
    )
    return cert
