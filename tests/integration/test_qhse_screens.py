"""Tests d'intégration — écran QHSE minimal (Phase 0.5).

Patron ``tests/integration/test_mrv_flgo_screens.py`` (coroutines de route
appelées directement, hors ASGI, avec ``db``/``FakeRequest`` de
``tests/integration/conftest.py``). Couvre : gate de permission
(``qhse:C``/``qhse:M``), écran hub, upload xlsx bout-en-bout, traçabilité
(``activity_log``).
"""

from __future__ import annotations

import io
from datetime import datetime
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.qhse import QhseReport
from app.models.user import User
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.validation_engine import invalidate_cache, seed_reference_data
from tests.integration.conftest import FakeRequest

_HEADER = [
    "Subject",
    "Description",
    "IssuedBy",
    "IssuedPlace",
    "Grade",
    "IssuedDate",
    "ClosedDate",
    "VesselName",
]


def _manager_user():
    return SimpleNamespace(id=40, full_name="QHSE Manager", username="qm", role="manager_maritime")


def _readonly_user():
    return SimpleNamespace(id=41, full_name="RH", username="rh1", role="rh")


def _upload(content: bytes, name: str = "qhse_export.xlsx") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def _build_xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_HEADER)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_qhse_routes_registered():
    from app.routers import qhse_router

    paths = {r.path for r in qhse_router.router.routes}
    assert "/qhse" in paths
    assert "/qhse/import" in paths


@pytest.mark.asyncio
async def test_qhse_index_requires_qhse_c(db):
    """Tous les rôles ont au moins C sur qhse (§7) — mais le checker doit
    quand même refuser un rôle inconnu de la matrice si jamais ajouté sans C."""
    checker = require_permission("qhse", "C")
    rh_user = _readonly_user()
    assert await checker(FakeRequest(), user=rh_user, db=db) is rh_user


@pytest.mark.asyncio
async def test_qhse_import_requires_qhse_m(db):
    """``rh`` n'a que ``qhse:C`` — l'import (M) doit refuser 403."""
    checker = require_permission("qhse", "M")
    rh_user = _readonly_user()
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=rh_user, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_qhse_index_renders(db):
    from app.routers.qhse_router import qhse_index

    resp = await qhse_index(FakeRequest(), db=db, user=_manager_user())
    assert resp.status_code == 200
    assert resp.context["total_reports"] == 0


@pytest.mark.asyncio
async def test_qhse_import_end_to_end_traced(db):
    from app.routers.qhse_router import qhse_import

    # RQ01-RQ03 doivent exister comme lignes ``validation_rules`` avant que
    # ``run_rules`` (désormais appelé par l'ingestion) puisse y référencer un
    # ``QualityCheckResult`` — même motif que ``test_mrv_qualite._seed``.
    invalidate_cache()
    await seed_reference_data(db)
    invalidate_cache()

    db.add(Vessel(code="ANE", name="Anemos"))
    # ``imported_by_user_id`` (D10) référence users.id pour de vrai — l'acteur
    # synthétique de ``_manager_user()`` a besoin d'une ligne correspondante.
    db.add(
        User(
            id=40,
            username="qm",
            email="qm@example.test",
            hashed_password="x",
            role="manager_maritime",
        )
    )
    await db.flush()

    content = _build_xlsx(
        [
            [
                "Mooring near miss",
                "Rope slipped",
                "TOWT MASTER ANEMOS",
                "At sea",
                "Near Miss / Hazard",
                datetime(2026, 1, 10),
                datetime(2026, 1, 15),
                "Anemos",
            ],
        ]
    )
    user = _manager_user()
    resp = await qhse_import(FakeRequest(), file=_upload(content), db=db, user=user)

    assert resp.status_code == 200
    report = resp.context["report"]
    assert report.imported == 1
    assert report.skipped == 0

    saved = (await db.execute(select(QhseReport))).scalar_one()
    assert saved.grade == "near_miss"

    log = (
        await db.execute(select(ActivityLog).where(ActivityLog.entity_type == "qhse_report"))
    ).scalar_one()
    assert log.action == "import"
    assert log.user_id == user.id
    assert log.module == "qhse"


@pytest.mark.asyncio
async def test_qhse_import_unresolved_vessel_is_skipped_not_crashed(db):
    from app.routers.qhse_router import qhse_import

    db.add(
        User(
            id=40,
            username="qm",
            email="qm@example.test",
            hashed_password="x",
            role="manager_maritime",
        )
    )
    await db.flush()

    content = _build_xlsx(
        [
            [
                "Deck slip",
                "slippery deck",
                "Crew",
                None,
                "Observation",
                datetime(2026, 3, 1),
                None,
                "Unknown Vessel",
            ],
        ]
    )
    resp = await qhse_import(FakeRequest(), file=_upload(content), db=db, user=_manager_user())
    report = resp.context["report"]
    assert report.imported == 0
    assert report.skipped == 1
    assert any("non reconnu" in e for e in report.errors)
