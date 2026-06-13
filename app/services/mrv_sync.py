"""MRV — synchronisation depuis les données du bord (FLX-03).

Le noon report est la **référence n°1** du reporting MRV : chaque noon
report génère un ``MRVEvent`` de type ``noon_consumption`` (fuel 24h,
ROB, distance 24h). Les SOF events mappés par ``SOF_TO_MRV_MAP``
génèrent leur événement MRV de phase (departure, arrival, anchoring,
bunkering).

Les deux fonctions sont **idempotentes** grâce aux liens uniques
``MRVEvent.noon_report_id`` / ``MRVEvent.sof_event_id``. Côté appelant,
elles sont invoquées best-effort : un échec de génération MRV ne doit
jamais faire échouer l'action du bord — mais il est loggé fort (donnée
réglementaire UE 2015/757).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mrv import MRVEvent, MRVParameter
from app.models.noon_report import NoonReport
from app.models.sof_event import SofEvent
from app.services.mrv_export import AVG_MDO_DENSITY_T_M3, map_sof_to_mrv_type

logger = logging.getLogger("mrv_sync")

_FALLBACK_MDO_DENSITY = Decimal(str(AVG_MDO_DENSITY_T_M3))  # t/m³


async def resolve_mdo_density(db: AsyncSession) -> Decimal:
    """Densité MDO (t/m³) — paramètre MRV ``avg_mdo_density``, sinon 0.845."""
    param = (
        await db.execute(
            select(MRVParameter).where(MRVParameter.name.ilike("%mdo_density%")).limit(1)
        )
    ).scalar_one_or_none()
    if param is not None and param.value is not None and Decimal(param.value) > 0:
        return Decimal(param.value)
    return _FALLBACK_MDO_DENSITY


def _dec(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def ensure_from_noon(db: AsyncSession, noon: NoonReport) -> MRVEvent | None:
    """Crée (ou retourne) le MRVEvent lié à un noon report. Idempotent."""
    existing = (
        await db.execute(select(MRVEvent).where(MRVEvent.noon_report_id == noon.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    volume_l = _dec(noon.fuel_consumed_24h_l)
    mass_t: Decimal | None = None
    if volume_l is not None:
        density = await resolve_mdo_density(db)
        mass_t = (volume_l * density / Decimal("1000")).quantize(Decimal("0.001"))

    ev = MRVEvent(
        leg_id=noon.leg_id,
        event_kind="noon_consumption",
        recorded_at=noon.recorded_at,
        fuel_type="MDO",
        fuel_volume_l=volume_l,
        fuel_mass_t=mass_t,
        rob_l=_dec(noon.rob_fuel_l),
        distance_nm=_dec(noon.distance_24h_nm),
        notes="Généré depuis noon report (référence n°1)",
        noon_report_id=noon.id,
    )
    # B5 — contrôle qualité ROB déclaré vs calculé (±2 t). Best-effort : ne
    # s'applique qu'à l'événement nouvellement créé (jamais sur le retour
    # de l'existant ci-dessus).
    await _apply_rob_quality(db, noon, ev)
    db.add(ev)
    await db.flush()
    return ev


# Seuil d'écart ROB toléré entre valeur déclarée et valeur calculée (tonnes).
_ROB_DEVIATION_THRESHOLD_T = Decimal("2.0")
# Densité MDO de référence pour la conversion litres → tonnes (1000 L = 1 m³).
_MDO_DENSITY_T_M3 = _FALLBACK_MDO_DENSITY


async def _apply_rob_quality(db: AsyncSession, noon: NoonReport, ev: MRVEvent) -> None:
    """Renseigne ``quality_status`` / ``quality_notes`` sur ``ev`` (B5).

    Compare le ROB déclaré au noon report au ROB calculé à partir du dernier
    point ROB connu sur le même leg, moins la consommation 24h. Écart toléré :
    ±2 t (densité MDO 0,845 t/m³).
    """
    prior = (
        await db.execute(
            select(MRVEvent)
            .where(
                MRVEvent.leg_id == noon.leg_id,
                MRVEvent.rob_l.is_not(None),
                MRVEvent.recorded_at < noon.recorded_at,
            )
            .order_by(MRVEvent.recorded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior is None:
        ev.quality_status = "ok"
        ev.quality_notes = "Premier point ROB du leg — pas de référence de calcul."
        return

    consumed_l = _dec(noon.fuel_consumed_24h_l) or Decimal("0")
    expected_rob_l = Decimal(prior.rob_l) - consumed_l
    declared_rob_l = _dec(noon.rob_fuel_l)

    if declared_rob_l is None:
        ev.quality_status = "warning"
        ev.quality_notes = "ROB déclaré manquant"
        return

    dev_t = abs(declared_rob_l - expected_rob_l) / Decimal("1000") * _MDO_DENSITY_T_M3
    if dev_t > _ROB_DEVIATION_THRESHOLD_T:
        ev.quality_status = "warning"
        ev.quality_notes = (
            f"Écart ROB {dev_t:.2f} t > 2 t "
            f"(déclaré {declared_rob_l} L vs calculé {expected_rob_l} L)."
        )
    else:
        ev.quality_status = "ok"
        ev.quality_notes = f"Écart ROB {dev_t:.2f} t (≤ 2 t)."


async def ensure_from_sof(db: AsyncSession, sof: SofEvent) -> MRVEvent | None:
    """Crée (ou retourne) le MRVEvent de phase lié à un SOF event mappé.

    Idempotent sur ``sof_event_id`` ; retourne None si le type SOF n'est
    pas mappé vers le MRV (cf. ``SOF_TO_MRV_MAP``).
    """
    kind = map_sof_to_mrv_type(sof.event_type)
    if kind is None:
        return None
    existing = (
        await db.execute(select(MRVEvent).where(MRVEvent.sof_event_id == sof.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    ev = MRVEvent(
        leg_id=sof.leg_id,
        event_kind=kind,
        recorded_at=sof.occurred_at,
        fuel_type="MDO",
        notes=f"Généré depuis SOF {sof.event_type}",
        sof_event_id=sof.id,
    )
    db.add(ev)
    await db.flush()
    return ev
