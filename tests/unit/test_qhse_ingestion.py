"""Tests — pipeline d'ingestion QHSE (Phase 0).

Couvre : import réussi + résolution navire/rapporteur/nettoyage IssuedPlace,
quarantaine (ClosedDate < IssuedDate, motif de test, navire non résolu), et
l'exécution du moteur de qualité générique (``validation_engine.run_rules``)
sur le scope ``qhse`` (RQ01-RQ03) — même moteur SQLite en mémoire que
``test_mrv_dataset.py``.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import openpyxl
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles contre Base.metadata
from app.database import Base
from app.models.qhse import QhseReport
from app.models.vessel import Vessel
from app.services.qhse_ingestion import import_qhse_xlsx
from app.services.validation_engine import RULES, invalidate_cache, run_rules, seed_reference_data

_HEADER = [
    "Subject",
    "Code",
    "Description",
    "IssuedBy",
    "Contact",
    "IssuedPlace",
    "Grade",
    "IssuedDate",
    "ClosedDate",
    "VesselName",
    "DescriptionAddedDate",
    "DescriptionAddedBy",
]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    invalidate_cache()
    await seed_reference_data(session)
    invalidate_cache()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        invalidate_cache()


def _workbook(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_HEADER)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_import_qhse_xlsx_happy_path_and_quarantine(db):
    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()

    rows = [
        # Valide : à importer, avec artefact [Sync] sur IssuedPlace à nettoyer.
        [
            "Mooring near miss",
            None,
            "Rope slipped_x000D_during mooring ops",
            "TOWT MASTER ANEMOS",
            None,
            "At sea[Sync1]",
            "Near Miss / Hazard",
            datetime(2026, 1, 10),
            datetime(2026, 1, 15),
            "Anemos",
            None,
            None,
        ],
        # Quarantaine RQ01 : ClosedDate antérieure à IssuedDate.
        [
            "Essai de non conformité",
            None,
            "test record",
            "QA",
            None,
            None,
            "Non Conformity",
            datetime(2026, 2, 1),
            datetime(2026, 1, 31),
            "Anemos",
            None,
            None,
        ],
        # Quarantaine RQ03 : navire non reconnu.
        [
            "Deck slip",
            None,
            "slippery deck",
            "Crew",
            None,
            None,
            "Observation",
            datetime(2026, 3, 1),
            None,
            "Unknown Vessel",
            None,
            None,
        ],
    ]
    report = await import_qhse_xlsx(db, _workbook(rows))

    assert report.imported == 1
    assert report.skipped == 2
    assert len(report.errors) == 2
    assert any("RQ01" in e or "ClosedDate" in e for e in report.errors)
    assert any("non reconnu" in e for e in report.errors)

    saved = (await db.execute(select(QhseReport))).scalars().all()
    assert len(saved) == 1
    assert saved[0].issued_place == "At sea"  # artefact [Sync1] retiré
    assert saved[0].grade == "near_miss"
    assert saved[0].description is not None
    assert "_x000D_" not in saved[0].description


def test_qhse_rules_registered():
    assert {"RQ01", "RQ02", "RQ03"}.issubset(RULES.keys())


async def test_run_rules_qhse_scope_flags_bad_subject(db):
    """Vérifie que le moteur générique détecte bien RQ01/RQ03 sur un sujet
    duck-typé qui n'est jamais passé par l'ingestion (ex. saisie manuelle
    future) — la réutilisation de ``validation_engine`` fonctionne bien pour
    le scope ``qhse`` sans aucune modification du moteur."""
    good = QhseReport(
        vessel_id=1,
        subject="Normal report",
        grade="observation",
        issued_date=datetime(2026, 1, 1, tzinfo=UTC),
        closed_date=datetime(2026, 1, 5, tzinfo=UTC),
    )
    bad_dates = QhseReport(
        vessel_id=1,
        subject="Bad dates",
        grade="observation",
        issued_date=datetime(2026, 1, 5, tzinfo=UTC),
        closed_date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    bad_vessel = QhseReport(
        vessel_id=None,
        subject="No vessel",
        grade="observation",
        issued_date=datetime(2026, 1, 1, tzinfo=UTC),
    )

    summary = await run_rules(db, "qhse", [good, bad_dates, bad_vessel])

    assert summary.total == 9  # 3 sujets x 3 règles (RQ01/RQ02/RQ03)
    assert summary.failed >= 2  # au moins RQ01 (bad_dates) + RQ03 (bad_vessel)
