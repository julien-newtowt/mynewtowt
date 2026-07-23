"""Scheduler interne — génération automatique du trombinoscope fin de mois.

Seul usage d'un scheduler **in-process** du projet (cf.
docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md) : toutes les autres
automatisations périodiques (météo, Marad, veille, tickets, devis, MRV)
passent par un cron **externe** (Power Automate) appelant un endpoint
token-protégé. Choix assumé pour éviter de dépendre d'un flux Power Automate
à configurer/maintenir côté IT pour cette seule fonctionnalité.

**Garde-fou multi-workers** : l'app tourne avec plusieurs workers uvicorn
(2 en production, cf. `Dockerfile`) qui démarreraient chacun leur propre
scheduler — sans protection, le job se déclencherait en double au même
instant. On utilise un verrou consultatif **transactionnel** Postgres
(`pg_try_advisory_xact_lock`, libéré automatiquement à la fin de la
transaction — pas de `pg_advisory_unlock` manuel, pas de risque de verrou
qui fuit si un worker crashe) : seul le worker qui l'obtient exécute la
génération. Une seconde vérification (une ligne `generated_reports`
automatique existe-t-elle déjà pour la période ?) protège en plus contre un
redémarrage de l'app le même jour.

L'endpoint manuel token-protégé (`POST /api/trombinoscope/generate`,
`app/routers/crew_router.py`) reste disponible comme déclencheur de secours
(tests, incident) — il n'est plus le mécanisme principal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, text

from app.config import settings
from app.database import SessionLocal
from app.models.generated_report import GeneratedReport
from app.services import crew_directory as directory_svc
from app.services import notifications, report_archive
from app.services.pdf_generator import render_crew_directory

logger = logging.getLogger("trombinoscope-scheduler")

# Clé arbitraire mais stable, dédiée à ce job (évite toute collision avec un
# éventuel autre pg_advisory_lock du projet).
_ADVISORY_LOCK_KEY = 725_190_442

_scheduler: AsyncIOScheduler | None = None


async def _generate_job() -> None:
    """Exécutée par APScheduler — ouvre sa propre session (hors requête HTTP)."""
    async with SessionLocal() as db:
        try:
            got_lock = (
                await db.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
                )
            ).scalar_one()
            if not got_lock:
                logger.info(
                    "Trombinoscope auto : verrou déjà détenu par un autre worker, génération ignorée."
                )
                return

            period = datetime.now(UTC).date()
            period_token = f"{period.year:04d}-{period.month:02d}"
            existing = (
                await db.execute(
                    select(GeneratedReport).where(
                        GeneratedReport.type == "trombinoscope",
                        GeneratedReport.period == period_token,
                        GeneratedReport.generated_by.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                logger.info(
                    "Trombinoscope auto déjà généré pour %s (report_id=%s), ignoré.",
                    period_token,
                    existing.id,
                )
                return

            directory_svc.invalidate_cache()
            directory = await directory_svc.build_directory(db)
            doc = render_crew_directory(directory=directory, period=period)
            report = await report_archive.save_report(
                db,
                pdf_bytes=doc.pdf,
                report_type="trombinoscope",
                period=period_token,
                generated_by=None,
            )
            await notifications.notify_trombinoscope_generated(db, period=period_token)
            await db.commit()  # libère aussi le verrou transactionnel
            logger.info(
                "Trombinoscope généré automatiquement (report_id=%s, période=%s, %d marins)",
                report.id,
                period_token,
                directory.member_count,
            )
        except Exception:
            await db.rollback()
            logger.exception("Échec de la génération automatique du trombinoscope")


def start() -> None:
    """Démarre le scheduler (no-op si déjà démarré ou désactivé par config)."""
    global _scheduler
    if _scheduler is not None or not settings.trombinoscope_scheduler_enabled:
        return
    _scheduler = AsyncIOScheduler(timezone=ZoneInfo("Europe/Paris"))
    _scheduler.add_job(
        _generate_job,
        CronTrigger(day="last", hour=23, minute=55),
        id="trombinoscope_monthly_generate",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info("Scheduler trombinoscope démarré (dernier jour du mois, 23:55 Europe/Paris).")


def shutdown() -> None:
    """Arrête le scheduler proprement (appelé au shutdown de l'app)."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
