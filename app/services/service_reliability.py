"""Taux de service (ponctualité) — actif de confiance post-reprise (P10).

Définition publiée : une traversée est « tenue » si son arrivée réelle (ATA)
tombe dans une fenêtre de ``ON_TIME_WINDOW_HOURS`` autour de l'arrivée
annoncée (ETA de référence contractuelle, ``leg.eta_ref``). Le taux =
traversées tenues / traversées arrivées (ATA renseignée).

Cette définition (|ATA − ETA_ref| < 24 h) est plus exigeante et plus honnête
que « pas en retard » : un navire très en avance n'est pas non plus « à
l'heure » du point de vue d'un chargeur qui planifie sa réception. La
méthodologie et la taille d'échantillon sont affichées à côté du chiffre
(doctrine anti-greenwashing : jamais de % sans base).

- Vitrine : bloc « Nos départs tenus » (landing) + ligne sur la fiche route,
  affichés seulement au-dessus d'un échantillon minimal (pas de « 100 % sur
  1 traversée »).
- Interne : ``is_below_floor`` lève le drapeau quand le taux passe sous 90 %,
  exploité par le dashboard exécutif.

Cache module 10 min (même régime que ``social_proof``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg

# Fenêtre de ponctualité (heures) autour de l'ETA de référence.
ON_TIME_WINDOW_HOURS = 24.0
_ON_TIME_WINDOW_SECONDS = ON_TIME_WINDOW_HOURS * 3600.0

# Échantillon minimal pour publier un taux (évite « 100 % sur 1 traversée »).
MIN_PUBLIC_SAMPLE = 5

# Seuil d'alerte interne (%). Sous ce plancher → drapeau exec.
ALERT_FLOOR_PCT = 90.0

_CACHE_TTL_SECONDS = 600.0


@dataclass(frozen=True)
class ReliabilityStats:
    """Ponctualité agrégée sur un ensemble de traversées arrivées."""

    completed: int  # traversées avec ATA renseignée
    on_time: int  # dont |ATA − ETA_ref| < fenêtre
    window_hours: float = ON_TIME_WINDOW_HOURS

    @property
    def pct(self) -> float | None:
        """Taux de service en %, ou ``None`` si aucune traversée arrivée."""
        if self.completed == 0:
            return None
        return round(self.on_time / self.completed * 100, 1)

    @property
    def is_publishable(self) -> bool:
        """Assez d'historique pour afficher le taux en vitrine."""
        return self.completed >= MIN_PUBLIC_SAMPLE

    @property
    def is_below_floor(self) -> bool:
        """Drapeau interne : taux publié sous le plancher d'alerte."""
        p = self.pct
        return p is not None and self.completed >= MIN_PUBLIC_SAMPLE and p < ALERT_FLOOR_PCT


def _tally(legs: list[Leg]) -> ReliabilityStats:
    completed = 0
    on_time = 0
    for leg in legs:
        if leg.ata is None or leg.eta_ref is None:
            continue
        completed += 1
        if abs((leg.ata - leg.eta_ref).total_seconds()) < _ON_TIME_WINDOW_SECONDS:
            on_time += 1
    return ReliabilityStats(completed=completed, on_time=on_time)


_overall_cache: ReliabilityStats | None = None
_overall_loaded_at: float = 0.0


def invalidate_cache() -> None:
    """Force le recalcul au prochain ``overall()`` (tests, admin)."""
    global _overall_cache, _overall_loaded_at
    _overall_cache = None
    _overall_loaded_at = 0.0


async def overall(db: AsyncSession) -> ReliabilityStats:
    """Ponctualité globale sur toutes les traversées arrivées (cache 10 min)."""
    global _overall_cache, _overall_loaded_at
    now = time.monotonic()
    if _overall_cache is not None and (now - _overall_loaded_at) < _CACHE_TTL_SECONDS:
        return _overall_cache
    try:
        legs = list((await db.execute(select(Leg).where(Leg.ata.is_not(None)))).scalars().all())
        stats = _tally(legs)
    except Exception:  # pragma: no cover — best-effort, la vitrine ne casse pas
        stats = ReliabilityStats(completed=0, on_time=0)
    _overall_cache = stats
    _overall_loaded_at = now
    return stats


async def for_route(
    db: AsyncSession, departure_port_id: int, arrival_port_id: int
) -> ReliabilityStats:
    """Ponctualité historique d'une ligne (même POL → POD). Non caché
    (volume faible, appelé sur une fiche route ponctuelle)."""
    try:
        legs = list(
            (
                await db.execute(
                    select(Leg)
                    .where(Leg.departure_port_id == departure_port_id)
                    .where(Leg.arrival_port_id == arrival_port_id)
                    .where(Leg.ata.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        return _tally(legs)
    except Exception:  # pragma: no cover
        return ReliabilityStats(completed=0, on_time=0)
