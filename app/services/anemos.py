"""Certificat Anemos — résolution de distance + émission du certificat.

L'émission est **idempotente** : un seul certificat par booking. Elle est
déclenchée par le cycle de vie du booking à ``discharged``/``delivered``
(cf. ``services/booking_lifecycle.py``) et peut aussi être appelée à la
demande.

ENV-03 — régularisation sur le réel déclaré :
- **distance** : Σ ``NoonReport.distance_24h_nm`` du leg quand le bord a
  déclaré (``distance_source = 'noon_reports'``), sinon distance
  planifiée (``'planned'``) résolue dans cet ordre :
  1. ``leg.distance_nm`` (persistée — source de vérité après 1ʳᵉ traversée) ;
  2. haversine depuis les coordonnées POL/POD (et on persiste le résultat
     sur le leg pour les fois suivantes) ;
  3. table de paires de ports en dur (fallback historique V3.0).
- **émissions NEWTOWT** : consommations déclarées à bord (Σ fuel noon
  reports × densité MDO × 3.206) allouées au booking pro-rata tonnage
  (``method = 'declared'``), sinon facteur forfaitaire 1,5 g/t·km
  (``method = 'theoretical'``).
- **référence conventionnelle** : toujours 13,7 g/t·km sur la même
  distance ; CO₂ évité = conventionnel − NEWTOWT, plancher à 0.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.leg import Leg
from app.models.noon_report import NoonReport
from app.models.port import Port
from app.services.activity import record as activity_record
from app.services.co2 import estimate as estimate_co2
from app.services.mrv_export import CO2_EMISSION_FACTOR_MDO
from app.services.mrv_sync import resolve_mdo_density
from app.services.ports import haversine_nm

# Statuts comptant dans la cargaison effective d'un leg (allocation pro-rata).
_ALLOCATION_STATUSES = ("confirmed", "loaded", "at_sea", "discharged", "delivered")

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


def resolve_distance_with_source(
    leg: Leg | None, pol: Port | None, pod: Port | None
) -> tuple[Decimal, str]:
    """(distance NM, source) — persistée → haversine → table → forfait.

    ``source`` ∈ {``leg_persisted``, ``haversine``, ``port_table``,
    ``unverified``}. ``unverified`` signale le repli forfaitaire (ports
    sans coordonnées et hors table) : il **doit** être mentionné sur tout
    document client (cf. certificat Anemos) — jamais silencieux.
    """
    if leg is not None and leg.distance_nm is not None:
        return Decimal(leg.distance_nm), "leg_persisted"
    if (
        pol is not None
        and pod is not None
        and pol.latitude is not None
        and pol.longitude is not None
        and pod.latitude is not None
        and pod.longitude is not None
    ):
        nm = haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude)
        return Decimal(str(round(nm, 2))), "haversine"
    pol_locode = getattr(pol, "locode", None)
    pod_locode = getattr(pod, "locode", None)
    if pol_locode and pod_locode and frozenset({pol_locode, pod_locode}) in _DISTANCE_NM:
        return _DISTANCE_NM[frozenset({pol_locode, pod_locode})], "port_table"
    return _DEFAULT_DISTANCE_NM, "unverified"


def resolve_distance_nm(leg: Leg | None, pol: Port | None, pod: Port | None) -> Decimal:
    """Distance NM pour un leg (cf. ``resolve_distance_with_source``)."""
    return resolve_distance_with_source(leg, pol, pod)[0]


async def _noon_total(db: AsyncSession, leg_id: int, column) -> Decimal:
    """Somme d'une colonne NoonReport sur un leg (0 si rien de déclaré)."""
    total = await db.scalar(
        select(func.coalesce(func.sum(column), 0)).where(NoonReport.leg_id == leg_id)
    )
    return Decimal(str(total or 0))


async def _booking_share(db: AsyncSession, booking: Booking) -> Decimal:
    """Part du booking dans la cargaison effective du leg.

    Pro-rata du tonnage (``total_weight_kg``) des bookings du leg en
    statut actif (confirmed → delivered) ; fallback pro-rata palettes si
    aucun poids n'est renseigné ; 1 si le leg n'a aucune cargaison
    dénombrable (le booking porte alors tout).
    """
    weight_total = Decimal(
        str(
            await db.scalar(
                select(func.coalesce(func.sum(Booking.total_weight_kg), 0))
                .where(Booking.leg_id == booking.leg_id)
                .where(Booking.status.in_(_ALLOCATION_STATUSES))
            )
            or 0
        )
    )
    if weight_total > 0:
        return (booking.total_weight_kg or Decimal("0")) / weight_total
    palettes_total = await db.scalar(
        select(func.coalesce(func.sum(Booking.total_palettes), 0))
        .where(Booking.leg_id == booking.leg_id)
        .where(Booking.status.in_(_ALLOCATION_STATUSES))
    )
    if palettes_total:
        return Decimal(booking.total_palettes or 0) / Decimal(palettes_total)
    return Decimal("1")


async def issue_for_booking(db: AsyncSession, booking: Booking) -> AnemosCertificate:
    """Crée (ou retourne) le certificat Anemos d'un booking. Idempotent.

    ENV-03 : émissions NEWTOWT sur le réel déclaré à bord quand il existe
    (``method = 'declared'``), sinon estimation forfaitaire
    (``'theoretical'``). Cf. docstring module pour le détail du calcul.
    """
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

    # a) Distance réelle parcourue (noon reports) sinon planifiée.
    noon_distance = await _noon_total(db, booking.leg_id, NoonReport.distance_24h_nm)
    if noon_distance > 0:
        distance = noon_distance.quantize(Decimal("0.01"))
        distance_source = "noon_reports"
    else:
        distance, distance_source = resolve_distance_with_source(leg, pol, pod)
        # Persiste la distance sur le leg seulement si elle est fiable
        # (jamais le forfait « unverified », qui doit rester signalé).
        if leg is not None and leg.distance_nm is None and distance_source != "unverified":
            leg.distance_nm = distance

    tonnage = (booking.total_weight_kg or Decimal("0")) / Decimal("1000")
    # Facteurs versionnés en base (ENV-02) — repli silencieux sur les constantes.
    from app.services.co2 import get_factors

    factors = await get_factors(db)
    emission = estimate_co2(distance_nm=distance, tonnage_t=tonnage, factors=factors)

    # b) Émissions NEWTOWT — réel déclaré à bord si disponible.
    fuel_l = await _noon_total(db, booking.leg_id, NoonReport.fuel_consumed_24h_l)
    if fuel_l > 0:
        density = await resolve_mdo_density(db)  # t/m³
        # litres × densité/1000 → tonnes fuel ; × 3.206 → t CO₂ ; × 1000 → kg.
        leg_co2_kg = fuel_l * density * Decimal(str(CO2_EMISSION_FACTOR_MDO))
        share = await _booking_share(db, booking)
        towt_kg = (leg_co2_kg * share).quantize(Decimal("0.001"))
        method = "declared"
    else:
        towt_kg = emission.towt_co2_kg
        method = "theoretical"

    # c) Référence conventionnelle : 13,7 g/t·km sur la même distance.
    conventional_kg = emission.conventional_co2_kg
    avoided_kg = max(conventional_kg - towt_kg, Decimal("0")).quantize(Decimal("0.001"))

    cert = AnemosCertificate(
        reference=f"ANEMOS-{booking.reference}",
        booking_id=booking.id,
        client_account_id=booking.client_account_id,
        leg_id=booking.leg_id,
        tonnage_transported_t=tonnage,
        distance_nm=distance,
        co2_emitted_kg=towt_kg,
        co2_conventional_kg=conventional_kg,
        co2_avoided_kg=avoided_kg,
        method=method,
        distance_source=distance_source,
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


# ---------------------------------------------------------------------------
# Reporting RSE annuel par client (ENV-06)
# ---------------------------------------------------------------------------


async def available_report_years(db: AsyncSession, client_account_id: int) -> list[int]:
    """Années pour lesquelles le client a au moins un label, plus récentes d'abord."""
    rows = (
        await db.execute(
            select(AnemosCertificate.issued_at).where(
                AnemosCertificate.client_account_id == client_account_id
            )
        )
    ).all()
    years = sorted({r[0].year for r in rows if r[0] is not None}, reverse=True)
    return years


async def annual_report(db: AsyncSession, *, client_account_id: int, year: int) -> dict:
    """Agrège les certificats Anemos d'un client sur une année.

    Retourne les totaux (tonnage, distance, CO₂ évité/émis/référence), le
    nombre d'expéditions, la part calculée sur données réelles déclarées
    (méthode « declared »), et la liste détaillée des expéditions — base du
    rapport RSE annuel téléchargeable (Bilan Carbone® scope 3 cat. 4).
    """
    res = await db.execute(
        select(AnemosCertificate, Booking.reference, Leg.leg_code)
        .join(Booking, Booking.id == AnemosCertificate.booking_id, isouter=True)
        .join(Leg, Leg.id == AnemosCertificate.leg_id, isouter=True)
        .where(AnemosCertificate.client_account_id == client_account_id)
        .order_by(AnemosCertificate.issued_at.asc())
    )
    rows = []
    tot_tonnage = Decimal("0")
    tot_distance = Decimal("0")
    tot_avoided = Decimal("0")
    tot_emitted = Decimal("0")
    tot_conventional = Decimal("0")
    declared_count = 0
    for cert, booking_ref, leg_code in res.all():
        if cert.issued_at is None or cert.issued_at.year != year:
            continue
        if cert.method == "declared":
            declared_count += 1
        tot_tonnage += cert.tonnage_transported_t or Decimal("0")
        tot_distance += cert.distance_nm or Decimal("0")
        tot_avoided += cert.co2_avoided_kg or Decimal("0")
        tot_emitted += cert.co2_emitted_kg or Decimal("0")
        tot_conventional += cert.co2_conventional_kg or Decimal("0")
        rows.append(
            {
                "reference": cert.reference,
                "booking_ref": booking_ref,
                "leg_code": leg_code,
                "issued_at": cert.issued_at,
                "tonnage_t": cert.tonnage_transported_t,
                "distance_nm": cert.distance_nm,
                "co2_avoided_kg": cert.co2_avoided_kg,
                "method": cert.method,
            }
        )
    return {
        "year": year,
        "shipments": rows,
        "shipment_count": len(rows),
        "declared_count": declared_count,
        "total_tonnage_t": tot_tonnage,
        "total_distance_nm": tot_distance,
        "total_avoided_kg": tot_avoided,
        "total_emitted_kg": tot_emitted,
        "total_conventional_kg": tot_conventional,
    }
