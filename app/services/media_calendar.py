"""Rétroplanning médias 2026–2027 (P12) — moments média dérivés du planning.

Deux familles de moments, dérivées **des données ERP existantes** (aucun modèle
dédié) :

* **Livraisons de navires** — les navires en construction (``Vessel``
  ``build_status == "under_construction"``) avec un horizon
  ``expected_delivery`` (« AAAA-MM » / « AAAA »). Ce sont les 4 livraisons de
  la flotte (Atlantis 07/2026, Atlas 09/2026, Archimedes 2027, Astérias 2027).
* **Arrivées café / cacao** — les legs qui transportent une origine café/cacao
  (booking avec ``coffee_origin`` renseigné), avec leur date d'arrivée (ATA à
  défaut ETA), le port et les origines concernées.

``build_moments`` est une **fonction pure** (déterministe, sans I/O) : elle
dérive et localise les moments à partir de lignes ``Vessel`` + descripteurs
d'arrivées. ``collect`` fait le travail base de données puis l'appelle.

La localisation des mois réutilise le référentiel flotte (source unique).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import t
from app.models.booking import Booking
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services import fleet, social_kit

# Un mois « fin d'année » pour trier les livraisons datées à l'année seule
# (« 2027ᵉ ») après tous les mois connus de la même année.
_YEAR_ONLY_MONTH = 13


@dataclass(frozen=True)
class Arrival:
    """Descripteur d'arrivée cargo (dérivé d'un leg + ses bookings café/cacao)."""

    leg_code: str
    vessel_name: str
    port_name: str | None
    arrival_at: datetime | None
    commodities: tuple[str, ...]  # sous-ensemble de ("coffee", "cacao")
    origin_labels: tuple[str, ...]


@dataclass(frozen=True)
class MediaMoment:
    """Un moment média, déjà localisé (titre / échéance / détail)."""

    kind: str  # "vessel_delivery" | "cargo_arrival"
    year: int
    month: int | None
    vessel_name: str
    date_label: str
    title: str
    detail: str
    sort_key: tuple[int, int, int, str]


@dataclass(frozen=True)
class MediaCalendar:
    """Rétroplanning trié chronologiquement, scindé par famille."""

    moments: tuple[MediaMoment, ...]

    @property
    def deliveries(self) -> tuple[MediaMoment, ...]:
        return tuple(m for m in self.moments if m.kind == "vessel_delivery")

    @property
    def arrivals(self) -> tuple[MediaMoment, ...]:
        return tuple(m for m in self.moments if m.kind == "cargo_arrival")

    @property
    def has_content(self) -> bool:
        return bool(self.moments)


def _month_label(year: int, month: int | None, lang: str) -> str:
    """« juillet 2026 » / « 2027 » (mois localisé via le référentiel flotte)."""
    if month is None:
        return str(year)
    months = fleet._MONTHS_BY_LANG.get((lang or "").lower(), fleet._MONTHS_BY_LANG["fr"])
    return f"{months[month - 1]} {year}"


def _commodity_label(commodities: Iterable[str], lang: str) -> str:
    """« Café » / « Cacao » / « Café / Cacao » (réutilise les clés social)."""
    labels = [t(f"social_commodity_{c}", lang) for c in commodities if c in ("coffee", "cacao")]
    return " / ".join(labels)


def build_moments(
    vessels: Iterable[Vessel],
    arrivals: Iterable[Arrival],
    *,
    lang: str = "fr",
) -> MediaCalendar:
    """Dérive et localise les moments média (fonction pure).

    Les livraisons proviennent des navires ``under_construction`` dotés d'un
    ``expected_delivery`` exploitable ; les arrivées des descripteurs
    ``Arrival``. Tri chronologique stable (année, mois, jour, libellé).
    """
    moments: list[MediaMoment] = []

    for v in vessels:
        if v.build_status != "under_construction":
            continue
        year, month = fleet._parse_delivery(v.expected_delivery)
        if year is None:
            continue
        moments.append(
            MediaMoment(
                kind="vessel_delivery",
                year=year,
                month=month,
                vessel_name=v.name,
                date_label=_month_label(year, month, lang),
                title=t("media_cal_delivery_title", lang, vessel=v.name),
                detail=t("media_cal_delivery_detail", lang),
                sort_key=(year, month or _YEAR_ONLY_MONTH, 0, v.name),
            )
        )

    for a in arrivals:
        if not a.commodities:
            continue
        at = a.arrival_at
        year = at.year if at else 0
        month = at.month if at else None
        day = at.day if at else 0
        date_label = at.strftime("%d/%m/%Y") if at else t("media_cal_undated", lang)
        detail_bits = [b for b in (a.port_name, ", ".join(a.origin_labels), a.leg_code) if b]
        moments.append(
            MediaMoment(
                kind="cargo_arrival",
                year=year,
                month=month,
                vessel_name=a.vessel_name,
                date_label=date_label,
                title=t(
                    "media_cal_arrival_title", lang, commodity=_commodity_label(a.commodities, lang)
                ),
                detail=" · ".join(detail_bits),
                sort_key=(year, month or _YEAR_ONLY_MONTH, day, a.leg_code),
            )
        )

    moments.sort(key=lambda m: m.sort_key)
    return MediaCalendar(moments=tuple(moments))


async def collect(db: AsyncSession, *, lang: str = "fr") -> MediaCalendar:
    """Assemble le rétroplanning depuis la base (navires + legs café/cacao)."""
    vessels = (
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code)))
        .scalars()
        .all()
    )

    rows = (
        await db.execute(
            select(
                Booking.coffee_origin,
                Leg.leg_code,
                Leg.ata,
                Leg.eta,
                Vessel.name,
                Port.name,
            )
            .join(Leg, Leg.id == Booking.leg_id)
            .join(Vessel, Vessel.id == Leg.vessel_id)
            .join(Port, Port.id == Leg.arrival_port_id)
            .where(Booking.coffee_origin.is_not(None))
        )
    ).all()

    # Agrège par leg (un leg peut porter plusieurs bookings / origines).
    agg: dict[str, dict] = {}
    for origin, leg_code, ata, eta, vessel_name, port_name in rows:
        commodity = social_kit.commodity_of(origin)
        if not commodity:
            continue
        entry = agg.setdefault(
            leg_code,
            {
                "vessel": vessel_name,
                "port": port_name,
                "arrival_at": ata or eta,
                "commodities": set(),
                "origins": set(),
            },
        )
        entry["commodities"].add(commodity)
        module = social_kit.resolve_origin(origin)
        if module:
            entry["origins"].add(module.origin_label(origin, lang))

    arrivals = [
        Arrival(
            leg_code=leg_code,
            vessel_name=data["vessel"],
            port_name=data["port"],
            arrival_at=data["arrival_at"],
            commodities=tuple(sorted(data["commodities"])),
            origin_labels=tuple(sorted(data["origins"])),
        )
        for leg_code, data in agg.items()
    ]

    return build_moments(vessels, arrivals, lang=lang)
