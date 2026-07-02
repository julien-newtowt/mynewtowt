"""Carnet de Bord ANEMOS - Service de génération.

Ce service est responsable de :
1. L'agrégation des données nécessaires pour le Carnet de Bord
2. La préparation des données pour les templates
3. La génération du PDF final

Le Carnet de Bord est généré à la demande pour un leg (préversion HTML ou
PDF), personnalisable par client (``client_account_id``).

Structure du Carnet de Bord (10 sections) :
0. Couverture
1. Introduction (ANEMOS, navire, route, philosophie)
2. Ch. 1 - La Traversée (carte + trace + ports + points remarquables)
3. Ch. 2 - L'équipage (module activable)
4. Ch. 3 - Le Chargement (personnalisé chargeur)
5. Ch. 4 - Conditions de transport (cale)
6. Ch. 5 - Performance environnementale
7. Ch. 6 - Performance de navigation
8. Ch. 7 - Conditions météorologiques (module activable)
9. Ch. 8 - Timeline complète
10. Conclusion
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.crew import CrewAssignment, CrewMember
from app.models.leg import Leg
from app.models.noon_report import (
    NoonReport,
    NoonReportEngine,
    NoonReportSail,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_highlight import VoyageHighlight
from app.models.voyage_photo import VoyagePhoto
from app.services import hold_conditions as hold_conditions_svc

# Propulsion modes
PROPULSION_MODES = ("sail", "assisted", "motor")

# ViewBox de la carte SVG du chapitre 1 (contrat du template
# ``chapitre_1_traversee.html`` : trace ``svg_path`` + points/ports ``.x/.y``).
MAP_W = 800
MAP_H = 400
MAP_PAD = 40


def _make_projection(points: list[tuple[float, float]]):
    """Projection équirectangulaire simple (lat/lon → viewBox du chapitre 1).

    Cadre la carte sur l'étendue des points fournis ; étendue dégénérée
    (un seul point, méridien unique…) → span de 1° pour éviter la division
    par zéro. Fonction pure, testable.
    """
    if not points:
        return lambda lat, lon: (MAP_W / 2.0, MAP_H / 2.0)
    lats = [lat for lat, _ in points]
    lons = [lon for _, lon in points]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_span = (lat_max - lat_min) or 1.0
    lon_span = (lon_max - lon_min) or 1.0
    inner_w = MAP_W - 2 * MAP_PAD
    inner_h = MAP_H - 2 * MAP_PAD

    def project(lat: float, lon: float) -> tuple[float, float]:
        x = MAP_PAD + (lon - lon_min) / lon_span * inner_w
        y = MAP_PAD + (1 - (lat - lat_min) / lat_span) * inner_h
        return (round(x, 1), round(y, 1))

    return project


def _project_route(data: CarnetBordData) -> None:
    """Projette trace GPS, points remarquables et ports pour la carte SVG.

    Le template du chapitre 1 consomme ``gps_trace[n].svg_path`` (segments
    ``M/L x,y`` joints en un ``<path d>``) et des attributs transitoires
    ``.x/.y`` posés sur les points remarquables et les ports (jamais
    persistés). Ce contrat n'avait jamais été implémenté côté service —
    la carte du carnet était donc ingénérable.
    """
    geo: list[tuple[float, float]] = [(p["latitude"], p["longitude"]) for p in data.gps_trace]
    geo += [(h.latitude, h.longitude) for h in data.highlights]
    for port in (data.pol, data.pod):
        if port is not None and port.latitude is not None and port.longitude is not None:
            geo.append((port.latitude, port.longitude))
    project = _make_projection(geo)

    for i, point in enumerate(data.gps_trace):
        x, y = project(point["latitude"], point["longitude"])
        point["x"] = x
        point["y"] = y
        point["svg_path"] = f"{'M' if i == 0 else 'L'} {x},{y}"

    for highlight in data.highlights:
        highlight.x, highlight.y = project(highlight.latitude, highlight.longitude)

    if data.pol is not None:
        if data.pol.latitude is not None and data.pol.longitude is not None:
            data.pol.x, data.pol.y = project(data.pol.latitude, data.pol.longitude)
        else:
            data.pol.x, data.pol.y = (MAP_PAD + 20.0, MAP_H / 2.0)
    if data.pod is not None:
        if data.pod.latitude is not None and data.pod.longitude is not None:
            data.pod.x, data.pod.y = project(data.pod.latitude, data.pod.longitude)
        else:
            data.pod.x, data.pod.y = (MAP_W - MAP_PAD - 20.0, MAP_H / 2.0)


class CarnetBordData:
    """Structure de données pour le Carnet de Bord."""

    def __init__(self):
        # Métadonnées générales
        self.leg: Leg | None = None
        self.vessel: Vessel | None = None
        self.pol: Port | None = None
        self.pod: Port | None = None
        self.client: ClientAccount | None = None
        self.generated_at: datetime = datetime.now(UTC)

        # Données par chapitre
        self.cover_photo: VoyagePhoto | None = None
        self.route_map_image: str | None = None
        self.anemos_logo: str | None = None

        # Chapitre 1 - La Traversée
        self.gps_trace: list[dict[str, Any]] = []
        self.highlights: list[VoyageHighlight] = []
        self.distance_nm: Decimal | None = None
        self.duration_days: float | None = None
        self.sog_avg: float | None = None
        self.sog_max: float | None = None
        self.propulsion_stats: dict[str, Any] = {}

        # Chapitre 2 - L'équipage
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

        # Chapitre 7 - Conditions météorologiques
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
    """Récupère toutes les données nécessaires pour générer le Carnet de Bord.

    Args:
        db: Session de base de données
        leg_id: ID du leg pour lequel générer le carnet
        client_account_id: ID du client (optionnel, pour personnalisation)

    Returns:
        CarnetBordData: Objet contenant toutes les données organisées par chapitre
    """
    data = CarnetBordData()

    # Récupération des données de base
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

    # Client (si spécifié ou depuis les bookings)
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
    # CHAPITRE 1 - La Traversée
    # =========================================================================

    # GPS Trace depuis NoonReports
    noon_reports = await db.execute(
        select(NoonReport).where(NoonReport.leg_id == leg_id).order_by(NoonReport.recorded_at)
    )
    noon_reports = noon_reports.scalars().all()

    if noon_reports:
        data.gps_trace = []
        for report in noon_reports:
            data.gps_trace.append(
                {
                    "latitude": report.latitude,
                    "longitude": report.longitude,
                    "recorded_at": report.recorded_at,
                    "propulsion_mode": report.propulsion_mode,
                    "sog": report.sog_avg,
                    "cog": report.cog_avg,
                }
            )

        # Calculer distance, durée, vitesses
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

    # Répartition propulsion
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

    # Carte SVG du chapitre 1 : projette trace, points remarquables et ports.
    _project_route(data)

    # =========================================================================
    # CHAPITRE 2 - L'équipage
    # =========================================================================

    # Membres d'équipage embarqués — la liaison passe par crew_assignments
    # (CREW-04 : un embarquement est rattaché au leg, ou au navire seul pour
    # les relèves hors leg). L'ancien code interrogeait des colonnes
    # inexistantes (CrewMember.vessel_id / last_name) : défaut latent corrigé.
    crew_ids = set(
        (
            await db.execute(
                select(CrewAssignment.crew_member_id).where(
                    (CrewAssignment.leg_id == leg_id)
                    | (
                        CrewAssignment.leg_id.is_(None)
                        & (CrewAssignment.vessel_id == leg.vessel_id)
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if crew_ids:
        crew_members = await db.execute(
            select(CrewMember).where(CrewMember.id.in_(crew_ids)).order_by(CrewMember.full_name)
        )
        data.crew_members = crew_members.scalars().all()

    # Photos d'équipage (batch "crew")
    crew_photos = await db.execute(
        select(VoyagePhoto)
        .where(VoyagePhoto.leg_id == leg_id)
        .where(VoyagePhoto.batch_id == "crew")
        .order_by(VoyagePhoto.display_order)
    )
    data.crew_photos = crew_photos.scalars().all()

    # Description de l'équipage depuis le vessel
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

        # Si un client est spécifié, calculer ses données
        if data.client:
            client_bookings = [b for b in bookings if b.client_account_id == data.client.id]
            if client_bookings:
                data.client_palettes = sum(b.total_palettes or 0 for b in client_bookings)
                data.client_weight_kg = sum(
                    Decimal(b.total_weight_kg or 0) for b in client_bookings
                )

        # Produits : agrégation par description de cargaison, depuis les lignes
        # de réservation (``BookingItem.cargo_description``). L'ancien code lisait
        # ``booking.goods_description`` — champ inexistant sur ``Booking`` (c'est
        # ``BookingItem.cargo_description``) : il aurait planté dès qu'un leg
        # portait des bookings. ``items`` est eager-load (selectin).
        product_map: dict[str, dict[str, Any]] = {}
        for booking in bookings:
            for item in booking.items:
                good = (item.cargo_description or "").strip()
                if not good:
                    continue
                if good not in product_map:
                    product_map[good] = {
                        "name": good,
                        "quantity": 0,
                        "weight_kg": Decimal("0"),
                    }
                product_map[good]["quantity"] += item.pallet_count or 0
                product_map[good]["weight_kg"] += Decimal(item.total_weight_kg or 0)

        if product_map:
            total_weight = sum(p["weight_kg"] for p in product_map.values())
            for product in product_map.values():
                product["pct"] = (
                    (product["weight_kg"] / total_weight * 100) if total_weight > 0 else 0
                )
            data.products = list(product_map.values())

        # Taux de remplissage (simplifié)
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

    # Agrégation unique des relevés de cale — service partagé avec l'espace
    # client, le portail expéditeur et la page publique de voyage. Corrige au
    # passage le calcul min/max historique (les relevés absents comptaient 0).
    conditions = await hold_conditions_svc.for_leg(db, leg_id)
    if conditions:
        data.hold_data = [
            {
                "name": h.location,
                "temp_min": h.temp_min,
                "temp_max": h.temp_max,
                "humidity_min": h.humidity_min,
                "humidity_max": h.humidity_max,
            }
            for h in conditions.holds
        ]
        data.temp_avg = conditions.temp_avg
        data.temp_min = conditions.temp_min
        data.temp_max = conditions.temp_max
        data.humidity_avg = conditions.humidity_avg
        data.humidity_min = conditions.humidity_min
        data.humidity_max = conditions.humidity_max

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

        # Calculer le taux de décarbonation
        if cert.co2_conventional_kg and cert.co2_conventional_kg > 0:
            data.decarbonation_rate = (cert.co2_avoided_kg / cert.co2_conventional_kg) * 100

    # Consommation depuis NoonReports
    if noon_reports:
        fuel_values = [r.fuel_consumed_24h_l for r in noon_reports if r.fuel_consumed_24h_l]
        if fuel_values:
            data.fuel_consumed_l = sum(fuel_values)

    # Facteurs par défaut
    data.towt_factor = 1.5  # g CO2/t.km
    data.conventional_factor = 13.7  # g CO2/t.km

    # =========================================================================
    # CHAPITRE 6 - Performance de navigation
    # =========================================================================

    # Temps de propulsion depuis NoonReportSail
    sail_rows = await db.execute(
        select(NoonReportSail).where(
            NoonReportSail.noon_report_id.in_([r.id for r in noon_reports])
        )
    )
    sail_rows = sail_rows.scalars().all()

    if sail_rows:
        # Compter les heures par mode (simplifié)
        # En réalité, il faudrait analyser les données plus finement
        for row in sail_rows:
            # Si les voiles sont utilisées
            if row.j0 or row.fwd_j1 or row.aft_j1 or row.fwd_ms or row.aft_ms:
                # Vérifier si les moteurs sont aussi utilisés
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

    # Données moteurs depuis NoonReportEngine
    engine_rows = await db.execute(
        select(NoonReportEngine).where(
            NoonReportEngine.noon_report_id.in_([r.id for r in noon_reports])
        )
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
    # CHAPITRE 7 - Conditions météorologiques
    # =========================================================================

    # Photos météo (batch "meteorology")
    weather_photos = await db.execute(
        select(VoyagePhoto)
        .where(VoyagePhoto.leg_id == leg_id)
        .where(VoyagePhoto.batch_id == "meteorology")
        .order_by(VoyagePhoto.display_order)
    )
    data.weather_images = weather_photos.scalars().all()

    # Statistiques météo depuis NoonReports
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

    # Événements de timeline (simplifié)
    # Pour l'instant, on crée des événements de base
    if leg:
        data.timeline_events = []

        # Départ
        if leg.atd:
            data.timeline_events.append(
                {
                    "event_type": "departure",
                    "event_category": "port",
                    "occurred_at": leg.atd,
                    "planned_at": leg.etd,
                    "location": data.pol.name if data.pol else "Port de départ",
                    "description": "Départ du port",
                }
            )

        # Arrivée
        if leg.ata:
            data.timeline_events.append(
                {
                    "event_type": "arrival",
                    "event_category": "port",
                    "occurred_at": leg.ata,
                    "planned_at": leg.eta,
                    "location": data.pod.name if data.pod else "Port d'arrivée",
                    "description": "Arrivée au port",
                }
            )

        # Points remarquables comme événements
        for highlight in data.highlights:
            data.timeline_events.append(
                {
                    "event_type": "highlight",
                    "event_category": highlight.category,
                    "occurred_at": highlight.occurred_at,
                    "location": f"Lat: {highlight.latitude}, Long: {highlight.longitude}",
                    "description": highlight.title,
                }
            )

        # Trier par date
        data.timeline_events.sort(
            key=lambda x: x["occurred_at"] if x["occurred_at"] else datetime.min
        )

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

    # Contacts (simplifié - à détailler)
    data.contacts = {
        "commercial": {
            "name": "Service Commercial NewTowt",
            "email": "commercial@newtowt.com",
            "phone": "+33 1 23 45 67 89",
        },
        "operations": {
            "name": "Service Opérations NewTowt",
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


def build_carnet_context(data: CarnetBordData, *, client_view: bool = False) -> dict[str, Any]:
    """Contexte de rendu complet du template ``pdf/carnet_bord.html``.

    Partagé entre la prévisualisation HTML (router) et la génération PDF :
    les attributs de :class:`CarnetBordData` portent exactement les noms de
    variables attendus par les templates de chapitres — le contexte est donc
    l'état complet de l'objet (le PDF recevait historiquement un contexte
    tronqué à 6 variables : défaut corrigé).

    ``client_view`` (vue téléchargée par un client depuis ``/me``) masque la
    ventilation cargo à l'échelle du navire (produits de TOUS les chargeurs
    du leg) — confidentialité inter-clients (cf. revue sécurité). La vue
    staff (défaut) reste complète.
    """
    from app.templating import brand_for_lang

    context = dict(vars(data))
    # Rendu hors-requête : le context processor n'injecte pas ``brand``,
    # dont dépend le pied de page @page de ``pdf/_base.html``, qui attend
    # aussi ``issued_at`` (date d'émission du document).
    context["brand"] = brand_for_lang("fr")
    context["issued_at"] = data.generated_at
    context["client_view"] = client_view
    return context


async def generate_carnet_bord_pdf(
    db: AsyncSession,
    leg_id: int,
    client_account_id: int | None = None,
    *,
    client_view: bool = False,
) -> bytes:
    """Génère le PDF du Carnet de Bord pour un leg (WeasyPrint).

    WeasyPrint est importé localement (dépendances natives lourdes) — même
    convention que ``services.pdf_generator``. ``client_view=True`` produit
    la version confidentielle (sans le mix cargo des co-chargeurs).
    """
    from weasyprint import HTML  # local import — heavy native deps

    from app.config import settings
    from app.templating import templates

    data = await get_carnet_bord_data(db, leg_id, client_account_id)
    context = build_carnet_context(data, client_view=client_view)
    html = templates.get_template("pdf/carnet_bord.html").render(**context)
    return HTML(string=html, base_url=settings.site_url or "").write_pdf()
