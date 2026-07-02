"""Référentiel flotte pour la vitrine publique — source ERP unique.

Le récit « 2 en opération, 4 en construction » et les horizons de livraison
ne sont jamais des chaînes statiques dans un template : ils sont dérivés des
lignes ``Vessel`` (``build_status`` + ``expected_delivery``). Modifier la
flotte dans l'ERP (ou le seed) met à jour la page ``/flotte`` sans toucher au
HTML — doctrine « aucune promesse publiée sans donnée ERP derrière ».

Cache module 10 min (la flotte évolue au rythme des livraisons, pas des
requêtes). Tolérant aux erreurs DB : la vitrine ne casse jamais faute de base.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vessel import Vessel

_CACHE_TTL_SECONDS = 600.0

# Noms de mois par langue (1→12). Le référentiel stocke un jeton neutre
# « AAAA-MM » ; la localisation se fait ici, au rendu, sans multiplier les
# clés i18n.
_MONTHS_BY_LANG: dict[str, tuple[str, ...]] = {
    "fr": (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "es": (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    "pt-br": (
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
    "vi": (
        "tháng 1",
        "tháng 2",
        "tháng 3",
        "tháng 4",
        "tháng 5",
        "tháng 6",
        "tháng 7",
        "tháng 8",
        "tháng 9",
        "tháng 10",
        "tháng 11",
        "tháng 12",
    ),
}


def _parse_delivery(token: str | None) -> tuple[int | None, int | None]:
    """« 2026-07 » → (2026, 7) ; « 2027 » → (2027, None) ; sinon (None, None)."""
    if not token:
        return (None, None)
    parts = token.split("-")
    try:
        year = int(parts[0])
    except (ValueError, IndexError):
        return (None, None)
    month: int | None = None
    if len(parts) > 1:
        try:
            m = int(parts[1])
            month = m if 1 <= m <= 12 else None
        except ValueError:
            month = None
    return (year, month)


@dataclass(frozen=True)
class FleetVessel:
    """Un navire tel que présenté au public (sans détail technique sensible)."""

    name: str
    build_status: str
    delivery_year: int | None
    delivery_month: int | None  # 1-12, ou None (année seule / navire en service)

    @property
    def is_operational(self) -> bool:
        return self.build_status == "operational"

    def delivery_label(self, lang: str | None) -> str | None:
        """Libellé localisé de l'horizon de livraison (ou None si non daté)."""
        if self.delivery_year is None:
            return None
        if self.delivery_month is None:
            return str(self.delivery_year)
        months = _MONTHS_BY_LANG.get((lang or "").lower(), _MONTHS_BY_LANG["fr"])
        return f"{months[self.delivery_month - 1]} {self.delivery_year}"


@dataclass(frozen=True)
class FleetRoster:
    """Flotte scindée en service / en construction, ordonnée par livraison."""

    operational: tuple[FleetVessel, ...]
    under_construction: tuple[FleetVessel, ...]

    @property
    def total(self) -> int:
        return len(self.operational) + len(self.under_construction)

    @property
    def operational_count(self) -> int:
        return len(self.operational)

    @property
    def under_construction_count(self) -> int:
        return len(self.under_construction)

    @property
    def has_content(self) -> bool:
        return self.total > 0


_roster_cache: FleetRoster | None = None
_roster_loaded_at: float = 0.0


def invalidate_cache() -> None:
    """Force le recalcul au prochain ``roster()`` (tests, admin, seed)."""
    global _roster_cache, _roster_loaded_at
    _roster_cache = None
    _roster_loaded_at = 0.0


async def roster(db: AsyncSession) -> FleetRoster:
    """Roster public de la flotte — cache 10 min, tolérant aux erreurs DB.

    Ordonné par ``Vessel.code`` : dans notre référentiel, l'ordre des codes
    suit la chronologie de mise en service puis de livraison (Anemos, Artemis
    en service ; Atlantis, Atlas, Archimedes, Astérias par échéance).
    """
    global _roster_cache, _roster_loaded_at
    now = time.monotonic()
    if _roster_cache is not None and (now - _roster_loaded_at) < _CACHE_TTL_SECONDS:
        return _roster_cache

    operational: list[FleetVessel] = []
    building: list[FleetVessel] = []
    try:
        rows = (
            (
                await db.execute(
                    select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code)
                )
            )
            .scalars()
            .all()
        )
        for v in rows:
            year, month = _parse_delivery(v.expected_delivery)
            fv = FleetVessel(
                name=v.name,
                build_status=v.build_status,
                delivery_year=year,
                delivery_month=month,
            )
            (operational if fv.is_operational else building).append(fv)
    except Exception:  # pragma: no cover — best-effort, la vitrine ne casse pas
        pass

    _roster_cache = FleetRoster(
        operational=tuple(operational),
        under_construction=tuple(building),
    )
    _roster_loaded_at = now
    return _roster_cache
