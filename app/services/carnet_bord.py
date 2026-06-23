"""Carnet de Bord ANEMOS - Service de gnration.

Ce service est responsable de :
1. L'agrgation des donnes ncessaires pour le Carnet de Bord
2. La prparation des donnes pour les templates
3. La gnration du PDF final

Le Carnet de Bord est gnr automatiquement  la fin d'un leg (quand le statut 
passera  "completed" ou "closed"). Il peut aussi tre gnr manuellement.

Structure du Carnet de Bord (10 sections) :
0. Couverture
1. Introduction (ANEMOS, navire, route, philosophie)
2. Ch. 1 - La Traverse (carte + trace + ports + points remarquables)
3. Ch. 2 - L'quipage (module activable)
4. Ch. 3 - Le Chargement (personnalis chargeur)
5. Ch. 4 - Conditions de transport (cale)
6. Ch. 5 - Performance environnementale
7. Ch. 6 - Performance de navigation
8. Ch. 7 - Conditions mtorologiques (module activable)
9. Ch. 8 - Timeline complte
10. Conclusion
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.crew import CrewMember
from app.models.leg import Leg
from app.models.noon_report import (
    NoonReport,
    NoonReportEngine,
    NoonReportHold,
    NoonReportSail,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_highlight import VoyageHighlight
from app.models.voyage_photo import VoyagePhoto, BATCH_CATEGORIES

# Propulsion modes
PROPULSION_MODES = ("sail", "assisted", "motor")


class CarnetBordData:
    """Structure de donnes pour le Carnet de Bord."""

    def __init__(self):
        # Mtadonnes gnrales
        self.leg: Leg | None = None
        self.vessel: Vessel | None = None
        self.pol: Port | None = None
        self.pod: Port | None = None
        self.client: ClientAccount | None = None
        self.generated_at: datetime = datetime.utcnow()

        # Donnes par chapitre
        self.cover_photo: VoyagePhoto | None = None
        self.route_map_image: str | None = None
        self.anemos_logo: str | None = None

        # Chapitre 1 - La Traverse
        self.gps_trace: list[dict[str, Any]] = []
        self.highlights: list[VoyageHighlight] = []
        self.distance_nm: Decimal | None = None
        self.duration_days: float | None = None
        self.sog_avg: float | None = None
        self.sog_max: float | None = None
        self.propulsion_stats: dict[str, Any] = {}

        # Chapitre 2 - L'quipage
        self.crew_members: list[CrewMember] = []
        self.crew_photos: list[VoyagePhoto] = []
        self.crew_description: str | None = None
        self.crew_org_chart: str | None = None
        self.watch_info: dict[str, Any] = {}

        # Chapitre 3 - Le Chargement
        self.total_palettes: int = 0
        self.client_palettes: int = 0
        self.total_weight_kg: Decimal = Decimal("0")
        self.client_weight_kg: Decimal = Decimal("0")
        self.fill_rate_surface: float | None = None
        self.fill_rate_weight: float | None = None
        self.products: list[dict[str, Any]] = []
        self.loading_photos: list[VoyagePhoto] = []

        # Chapitre 4 - Conditions de transport
        self.hold_data: list[dict[str, Any]] = []
        self.temp_avg: float | None = None
        self.temp_min: float | None = None
        self.temp_max: float | None = None
        self.humidity_avg: float | None = None
        self.humidity_min: float | None = None
        self.humidity_max: float | None = None
        self.temp_chart: str | None = None
        self.hold_comments: str | None = None

        # Chapitre 5 - Performance environnementale
        self.co2_avoided_kg: Decimal | None = None
        self.co2_emitted_kg: Decimal | None = None
        self.co2_conventional_kg: Decimal | None = None
        self.decarbonation_rate: float | None = None
        self.fuel_consumed_l: float | None = None
        self.emission_rate: float | None = None
        self.towt_factor: float | None = None
        self.conventional_factor: float | None = None
        self.method: str | None = None
        self.distance_source: str | None = None
        self.verification_statement: str | None = None

        # Chapitre 6 - Performance de navigation
        self.sailing_hours: float = 0
        self.assisted_hours: float = 0
        self.motor_hours: float = 0
        self.total_hours: float = 0
        self.sail_pct: float = 0
        self.assisted_pct: float = 0
        self.motor_pct: float = 0
        self.engine_data: list[dict[str, Any]] = []
        self.sail_trim_data: list[dict[str, Any]] = []

        # Chapitre 7 - Conditions mtorologiques
        self.weather_images: list[VoyagePhoto] = []
        self.weather_stats: dict[str, Any] = {}
        self.weather_events: list[dict[str, Any]] = []

        # Chapitre 8 - Timeline
        self.timeline_events: list[dict[str, Any]] = []
        self.timeline_stats: dict[str, Any] = {}
        self.etd_eta_info: dict[str, Any] = {}

        # Conclusion
        self.conclusion_message: str | None = None
        self.upcoming_legs: list[Leg] = []
        self.contacts: dict[str, Any] = {}
        self.qr_album: str | None = None
        self.qr_album_image: str | None = None
        self.qr_anemos: str | None = None
        self.qr_anemos_image: str | None = None


async def get_carnet_bord_data(
    db: AsyncSession,
    leg_id: int,
    client_account_id: int | None = None,
) -> CarnetBordData:
    """Rcupre toutes les donnes ncessaires pour gnrer le Carnet de Bord.

    Args:
        db: Session de base de donnes
        leg_id: ID du leg pour lequel gnrer le carnet
        client_account_id: ID du client (optionnel, pour personnalisation)

    Returns:
        CarnetBordData: Objet contenant toutes les donnes organises par chapitre
    """
    data = CarnetBordData()

    # Rcupration des donnes de base
    leg = await db.get(Leg, leg_id)
    if not leg:
        return data

    data.leg = leg

    # Vessel
    vessel = await db.get(Vessel, leg.vessel_id)
    data.vessel = vessel

    # Ports
    data.pol = await db.get(Port, leg.departure_port_id)
    data.pod = await db.get(Port, leg.arrival_port_id)

    # Client (si spcifi ou depuis les bookings)
    if client_account_id:
        data.client = await db.get(ClientAccount, client_account_id)
    else:
        # Trouver le client principal depuis les bookings
        bookings = await db.execute(
            select(Booking)
            .where(Booking.leg_id == leg_id)
            .where(Booking.status.in_(["confirmed", "loaded", "at_sea", "discharged", "delivered"]))
            .order_by(Booking.total_weight_kg.desc())
            .limit(1)
        )
        booking = bookings.scalar_one_or_none()
        if booking:
            data.client = await db.get(ClientAccount, booking.client_account_id)

    # =========================================================================
    # CHAPITRE 1 - La Traverse
    # =========================================================================

    # GPS Trace depuis NoonReports
    noon_reports = await db.execute(
        select(NoonReport)
        .where(NoonReport.leg_id == leg_id)
        .order_by(NoonReport.recorded_at)
    )
    noon_reports = noon_reports.scalars().all()

    if noon_reports:
        data.gps_trace = []
        for report in noon_reports:
            data.gps_trace.append({
                "latitude": report.latitude,
                "longitude": report.longitude,
                "recorded_at": report.recorded_at,
                "propulsion_mode": report.propulsion_mode,
                "sog": report.sog_avg,
                "cog": report.cog_avg,
            })

        # Calculer distance, dure, vitesses
        if leg.distance_nm:
            data.distance_nm = Decimal(leg.distance_nm)

        if leg.atd and leg.ata:
            data.duration_days = (leg.ata - leg.atd).total_seconds() / 86400

        # SOG avg/max
        sog_values = [r.sog_avg for r in noon_reports if r.sog_avg]
        if sog_values:
            data.sog_avg = sum(sog_values) / len(sog_values)

        sog_max_values = [r.sog_max for r in noon_reports if r.sog_max]
        if sog_max_values:
            data.sog_max = max(sog_max_values)

    # Points remarquables
    highlights = await db.execute(
        select(VoyageHighlight)
        .where(VoyageHighlight.leg_id == leg_id)
        .order_by(VoyageHighlight.display_order, VoyageHighlight.occurred_at)
    )
    data.highlights = highlights.scalars().all()

    # Rpartition propulsion
    propulsion_counts: dict[str, int] = {"sail": 0, "assisted": 0, "motor": 0}
    for report in noon_reports:
        mode = report.propulsion_mode
        if mode in propulsion_counts:
            propulsion_counts[mode] += 1

    total_points = sum(propulsion_counts.values())
    if total_points > 0:
        data.propulsion_stats = {
            "sail_pct": (propulsion_counts["sail"] / total_points) * 100,
            "assisted_pct": (propulsion_counts["assisted"] / total_points) * 100,
            "motor_pct": (propulsion_counts["motor"] / total_points) * 100,
        }

    # =========================================================================
    # CHAPITRE 2 - L'quipage
    # =========================================================================

    # Membres d'quipage
    crew_members = await db.execute(
        select(CrewMember)
        .where(CrewMember.vessel_id == leg.vessel_id)
        .order_by(CrewMember.last_name, CrewMember.first_name)
    )
    data.crew_members = crew_members.scalars().all()

    # Photos d'quipage (batch "crew")
    crew_photos = await db.execute(
        select(VoyagePhoto)
        .where(VoyagePhoto.leg_id == leg_id)
        .where(VoyagePhoto.batch_id == "crew")
        .order_by(VoyagePhoto.display_order)
    )
    data.crew_photos = crew_photos.scalars().all()

    # Description de l'quipage depuis le vessel
    if vessel:
        data.crew_description = vessel.crew_description

    # =========================================================================
    # CHAPITRE 3 - Le Chargement
    # =========================================================================

    # Bookings pour ce leg
    bookings = await db.execute(
        select(Booking)
        .where(Booking.leg_id == leg_id)
        .where(Booking.status.in_(["confirmed", "loaded", "at_sea", "discharged", "delivered"]))
    )
    bookings = bookings.scalars().all()

    if bookings:
        data.total_palettes = sum(b.total_palettes or 0 for b in bookings)
        data.total_weight_kg = sum(Decimal(b.total_weight_kg or 0) for b in bookings)

        # Si un client est spcifi, calculer ses donnes
        if data.client:
            client_bookings = [b for b in bookings if b.client_account_id == data.client.id]
            if client_bookings:
                data.client_palettes = sum(b.total_palettes or 0 for b in client_bookings)
                data.client_weight_kg = sum(
                    Decimal(b.total_weight_kg or 0) for b in client_bookings
                )

        # Produits (simplifi -  dtailler)
        # Pour l'instant, on groupe par type de produit
        product_map: dict[str, dict[str, Any]] = {}
        for booking in bookings:
            if booking.goods_description:
                for good in booking.goods_description.split(","):
                    good = good.strip()
                    if good not in product_map:
                        product_map[good] = {
                            "name": good,
                            "quantity": 0,
                            "weight_kg": Decimal("0"),
                        }
                    product_map[good]["quantity"] += booking.total_palettes or 0
                    product_map[good]["weight_kg"] += Decimal(booking.total_weight_kg or 0)

        if product_map:
            total_weight = sum(p["weight_kg"] for p in product_map.values())
            for product in product_map.values():
                product["pct"] = (product["weight_kg"] / total_weight * 100) if total_weight > 0 else 0
            data.products = list(product_map.values())

        # Taux de remplissage (simplifi)
        if vessel and vessel.capacity_palettes and vessel.capacity_palettes > 0:
            data.fill_rate_surface = (data.total_palettes / vessel.capacity_palettes) * 100

        # Photos de chargement
        loading_photos = await db.execute(
            select(VoyagePhoto)
            .where(VoyagePhoto.leg_id == leg_id)
            .where(VoyagePhoto.batch_id.in_(["loading", "cargo", "port_pol"]))
            .order_by(VoyagePhoto.display_order)
        )
        data.loading_photos = loading_photos.scalars().all()

    # =========================================================================
    # CHAPITRE 4 - Conditions de transport
    # =========================================================================

    # Donnes des cales depuis NoonReportHold
    hold_rows = await db.execute(
        select(NoonReportHold)
        .where(NoonReportHold.noon_report_id.in_([r.id for r in noon_reports]))
    )
    hold_rows = hold_rows.scalars().all()

    if hold_rows:
        # Grouper par zone
        zone_data: dict[str, dict[str, Any]] = {}
        for row in hold_rows:
            zone = row.location
            if zone not in zone_data:
                zone_data[zone] = {
                    "name": zone,
                    "temp_min": row.temp_midnight_c or row.temp_midday_c,
                    "temp_max": row.temp_midnight_c or row.temp_midday_c,
                    "humidity_min": row.humidity_midnight_pct or row.humidity_midday_pct,
                    "humidity_max": row.humidity_midnight_pct or row.humidity_midday_pct,
                    "temps": [],
                    "humidities": [],
                }
            else:
                zone_data[zone]["temp_min"] = min(
                    zone_data[zone]["temp_min"] or 0,
                    row.temp_midnight_c or 0,
                    row.temp_midday_c or 0
                )
                zone_data[zone]["temp_max"] = max(
                    zone_data[zone]["temp_max"] or 0,
                    row.temp_midnight_c or 0,
                    row.temp_midday_c or 0
                )
                zone_data[zone]["humidity_min"] = min(
                    zone_data[zone]["humidity_min"] or 0,
                    row.humidity_midnight_pct or 0,
                    row.humidity_midday_pct or 0
                )
                zone_data[zone]["humidity_max"] = max(
                    zone_data[zone]["humidity_max"] or 0,
                    row.humidity_midnight_pct or 0,
                    row.humidity_midday_pct or 0
                )

        data.hold_data = list(zone_data.values())

        # Calculer les stats globales
        all_temps = []
        all_humidities = []
        for row in hold_rows:
            if row.temp_midnight_c:
                all_temps.append(row.temp_midnight_c)
            if row.temp_midday_c:
                all_temps.append(row.temp_midday_c)
            if row.humidity_midnight_pct:
                all_humidities.append(row.humidity_midnight_pct)
            if row.humidity_midday_pct:
                all_humidities.append(row.humidity_midday_pct)

        if all_temps:
            data.temp_avg = sum(all_temps) / len(all_temps)
            data.temp_min = min(all_temps)
            data.temp_max = max(all_temps)

        if all_humidities:
            data.humidity_avg = sum(all_humidities) / len(all_humidities)
            data.humidity_min = min(all_humidities)
            data.humidity_max = max(all_humidities)

    # =========================================================================
    # CHAPITRE 5 - Performance environnementale
    # =========================================================================

    # Certificat ANEMOS si disponible
    if client_account_id:
        cert = await db.execute(
            select(AnemosCertificate)
            .where(AnemosCertificate.booking_id.in_([b.id for b in bookings]))
            .where(AnemosCertificate.client_account_id == client_account_id)
            .order_by(AnemosCertificate.issued_at.desc())
            .limit(1)
        )
        cert = cert.scalar_one_or_none()
    else:
        cert = await db.execute(
            select(AnemosCertificate)
            .where(AnemosCertificate.leg_id == leg_id)
            .order_by(AnemosCertificate.issued_at.desc())
            .limit(1)
        )
        cert = cert.scalar_one_or_none()

    if cert:
        data.co2_avoided_kg = cert.co2_avoided_kg
        data.co2_emitted_kg = cert.co2_emitted_kg
        data.co2_conventional_kg = cert.co2_conventional_kg
        data.method = cert.method
        data.distance_source = cert.distance_source

        # Calculer le taux de dcarbonation
        if cert.co2_conventional_kg and cert.co2_conventional_kg > 0:
            data.decarbonation_rate = (
                (cert.co2_avoided_kg / cert.co2_conventional_kg) * 100
            )

    # Consommation depuis NoonReports
    if noon_reports:
        fuel_values = [r.fuel_consumed_24h_l for r in noon_reports if r.fuel_consumed_24h_l]
        if fuel_values:
            data.fuel_consumed_l = sum(fuel_values)

    # Facteurs par dfaut
    data.towt_factor = 1.5  # g CO2/t.km
    data.conventional_factor = 13.7  # g CO2/t.km

    # =========================================================================
    # CHAPITRE 6 - Performance de navigation
    # =========================================================================

    # Temps de propulsion depuis NoonReportSail
    sail_rows = await db.execute(
        select(NoonReportSail)
        .where(NoonReportSail.noon_report_id.in_([r.id for r in noon_reports]))
    )
    sail_rows = sail_rows.scalars().all()

    if sail_rows:
        # Compter les heures par mode (simplifi)
        # En ralit, il faudrait analyser les donnes plus finement
        for row in sail_rows:
            # Si les voiles sont utilises
            if row.j0 or row.fwd_j1 or row.aft_j1 or row.fwd_ms or row.aft_ms:
                # Vrifier si les moteurs sont aussi utiliss
                if row.me_ps_load_pct or row.me_sb_load_pct:
                    data.assisted_hours += 24  # Approximation
                else:
                    data.sailing_hours += 24
            else:
                data.motor_hours += 24

        data.total_hours = data.sailing_hours + data.assisted_hours + data.motor_hours

        if data.total_hours > 0:
            data.sail_pct = (data.sailing_hours / data.total_hours) * 100
            data.assisted_pct = (data.assisted_hours / data.total_hours) * 100
            data.motor_pct = (data.motor_hours / data.total_hours) * 100

    # Donnes moteurs depuis NoonReportEngine
    engine_rows = await db.execute(
        select(NoonReportEngine)
        .where(NoonReportEngine.noon_report_id.in_([r.id for r in noon_reports]))
    )
    engine_rows = engine_rows.scalars().all()

    if engine_rows:
        # Grouper par moteur
        engine_map: dict[str, dict[str, Any]] = {}
        for row in engine_rows:
            if row.engine not in engine_map:
                engine_map[row.engine] = {
                    "name": row.engine,
                    "load_pct": [],
                    "hours": [],
                }
            if row.running_hours_h:
                engine_map[row.engine]["hours"].append(row.running_hours_h)

        for engine in engine_map.values():
            if engine["hours"]:
                engine["hours_total"] = sum(engine["hours"])
            else:
                engine["hours_total"] = 0

        data.engine_data = list(engine_map.values())

    # =========================================================================
    # CHAPITRE 7 - Conditions mtorologiques
    # =========================================================================

    # Photos mto (batch "meteorology")
    weather_photos = await db.execute(
        select(VoyagePhoto)
        .where(VoyagePhoto.leg_id == leg_id)
        .where(VoyagePhoto.batch_id == "meteorology")
        .order_by(VoyagePhoto.display_order)
    )
    data.weather_images = weather_photos.scalars().all()

    # Statistiques mto depuis NoonReports
    if noon_reports:
        wind_speeds = [r.wind_speed_kn for r in noon_reports if r.wind_speed_kn]
        if wind_speeds:
            data.weather_stats = {
                "avg_wind_speed": sum(wind_speeds) / len(wind_speeds),
                "max_wind_speed": max(wind_speeds),
            }

        sea_states = [r.sea_state_bf for r in noon_reports if r.sea_state_bf]
        if sea_states:
            data.weather_stats["avg_sea_state"] = sum(sea_states) / len(sea_states)

        visibilities = [r.visibility_nm for r in noon_reports if r.visibility_nm]
        if visibilities:
            data.weather_stats["avg_visibility"] = sum(visibilities) / len(visibilities)

    # =========================================================================
    # CHAPITRE 8 - Timeline
    # =========================================================================

    # vnements de timeline (simplifi)
    # Pour l'instant, on cre des vnements de base
    if leg:
        data.timeline_events = []

        # Dpart
        if leg.atd:
            data.timeline_events.append({
                "event_type": "departure",
                "event_category": "port",
                "occurred_at": leg.atd,
                "planned_at": leg.etd,
                "location": data.pol.name if data.pol else "Port de dpart",
                "description": "Dpart du port",
            })

        # Arrive
        if leg.ata:
            data.timeline_events.append({
                "event_type": "arrival",
                "event_category": "port",
                "occurred_at": leg.ata,
                "planned_at": leg.eta,
                "location": data.pod.name if data.pod else "Port d'arrive",
                "description": "Arrive au port",
            })

        # Points remarquables comme vnements
        for highlight in data.highlights:
            data.timeline_events.append({
                "event_type": "highlight",
                "event_category": highlight.category,
                "occurred_at": highlight.occurred_at,
                "location": f"Lat: {highlight.latitude}, Long: {highlight.longitude}",
                "description": highlight.title,
            })

        # Trier par date
        data.timeline_events.sort(key=lambda x: x["occurred_at"] if x["occurred_at"] else datetime.min)

    # ETD/ETA info
    if leg:
        data.etd_eta_info = {
            "etd_planned": leg.etd,
            "etd_actual": leg.atd,
            "eta_planned": leg.eta,
            "eta_actual": leg.ata,
        }

    # =========================================================================
    # CONCLUSION
    # =========================================================================

    # Prochains legs
    upcoming = await db.execute(
        select(Leg)
        .where(Leg.vessel_id == leg.vessel_id)
        .where(Leg.etd > leg.etd)
        .order_by(Leg.etd)
        .limit(5)
    )
    data.upcoming_legs = upcoming.scalars().all()

    # Contacts (simplifi -  dtailler)
    data.contacts = {
        "commercial": {
            "name": "Service Commercial NewTowt",
            "email": "commercial@newtowt.com",
            "phone": "+33 1 23 45 67 89",
        },
        "operations": {
            "name": "Service Oprations NewTowt",
            "email": "operations@newtowt.com",
            "phone": "+33 1 23 45 67 90",
        },
        "technical": {
            "name": "Service Technique NewTowt",
            "email": "technical@newtowt.com",
            "phone": "+33 1 23 45 67 91",
        },
    }

    return data


async def generate_carnet_bord_pdf(
    db: AsyncSession,
    leg_id: int,
    client_account_id: int | None = None,
) -> bytes:
    """Gnre le PDF du Carnet de Bord pour un leg.

    Args:
        db: Session de base de donnes
        leg_id: ID du leg
        client_account_id: ID du client pour personnalisation

    Returns:
        bytes: Contenu du PDF gnr
    """
    from app.services.pdf import render_pdf
    from app.templating import render_template

    # Rcuprer les donnes
    data = await get_carnet_bord_data(db, leg_id, client_account_id)

    # Prparer le contexte pour le template
    context = {
        "leg": data.leg,
        "vessel": data.vessel,
        "pol": data.pol,
        "pod": data.pod,
        "client": data.client,
        "generated_at": data.generated_at,
        # Ajouter toutes les autres donnes...
    }

    # Rendre le template
    html = await render_template("pdf/carnet_bord.html", **context)

    # Gnrer le PDF
    pdf_bytes = await render_pdf(html)

    return pdf_bytes
