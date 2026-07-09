"""Tests d'intégration — écran FLGO (Marad, lecture seule), MRV LOT 7.

Patron ``tests/integration/test_bunkering_screens.py`` (coroutines de route
appelées directement, hors ASGI, avec ``db``/``FakeRequest`` de
``tests/integration/conftest.py``). Couvre : gate de permission
(``mrv:C``/``mrv:M``), écran liste (filtres + indicateur de cohérence
interne R25), upload xlsx bout-en-bout (fichier généré par openpyxl dans le
test), traçabilité (``activity_log``).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.flgo import FlgoReading
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import flgo_sync as fs
from tests.integration.conftest import FakeRequest


async def _vessel(db, code: str = "ANE", name: str = "Anemos") -> Vessel:
    v = Vessel(code=code, name=name)
    db.add(v)
    await db.flush()
    return v


def _mrv_viewer_user():
    return SimpleNamespace(id=30, full_name="MRV Viewer", username="mrvc", role="data_analyst")


def _mrv_editor_user():
    return SimpleNamespace(id=31, full_name="MRV Editor", username="mrvm", role="operation")


def _upload(content: bytes, name: str = "flgo_export.xlsx") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def _build_xlsx(rows: list[list], product: str = "Diesel Oil") -> bytes:
    """Petit export IHM FLGO généré par openpyxl (2 compartiments), même
    structure que les exports réels Anemos_All*.xlsx / FLGO {Anemos,Artemis}.xlsx."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main sheet"
    ws.append(["NewTOWT"])
    ws.append([])
    ws.append(["TestVessel"])
    ws.append(["All - test range"])
    ws.append([None, "Product"])
    ws.append([None, "Category: Fuel"])
    ws.append([None, product])
    ws.append(
        [
            None,
            None,
            "",
            "Operation date",
            "14 - GO DB B",
            "15 - GO DB T",
            "Total volume [m3]",
            "ROB [m3]",
            "Remarks",
            "Docs",
        ]
    )
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ═══════════════════════════════════════════════════ routes enregistrées


def test_flgo_routes_registered():
    from app.routers import mrv_router

    paths = {r.path for r in mrv_router.router.routes}
    assert "/mrv/flgo" in paths
    assert "/mrv/flgo/import" in paths


# ═══════════════════════════════════════════════════ gate de permission


@pytest.mark.asyncio
async def test_mrv_flgo_index_requires_mrv_c(db):
    """Un rôle sans accès ``mrv`` (ex. rh) reçoit 403 en consultation."""
    checker = require_permission("mrv", "C")
    rh_user = SimpleNamespace(id=98, full_name="RH", username="rh1", role="rh")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=rh_user, db=db)
    assert exc.value.status_code == 403

    viewer = SimpleNamespace(id=97, full_name="Data", username="da", role="data_analyst")
    assert await checker(FakeRequest(), user=viewer, db=db) is viewer


@pytest.mark.asyncio
async def test_mrv_flgo_import_requires_mrv_m(db):
    """``armement`` n'a que ``mrv:C`` — l'import (M) doit refuser 403."""
    checker = require_permission("mrv", "M")
    armement_user = SimpleNamespace(id=96, full_name="Armement", username="arm", role="armement")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=armement_user, db=db)
    assert exc.value.status_code == 403


# ═══════════════════════════════════════════════════ écran liste


@pytest.mark.asyncio
async def test_mrv_flgo_index_renders_empty(db):
    from app.routers.mrv_router import mrv_flgo_index

    resp = await mrv_flgo_index(
        FakeRequest(),
        vessel_id=None,
        action_type=None,
        source=None,
        date_from=None,
        date_to=None,
        db=db,
        user=_mrv_viewer_user(),
    )
    assert resp.status_code == 200
    assert resp.context["rows"] == []


@pytest.mark.asyncio
async def test_mrv_flgo_index_filters_by_vessel_and_type(db):
    from app.routers.mrv_router import mrv_flgo_index

    v1 = await _vessel(db, code="ANE", name="Anemos")
    v2 = await _vessel(db, code="ART", name="Artemis")
    await fs._upsert_reading(
        db,
        vessel_id=v1.id,
        action_type="measurement",
        product_name="Diesel Oil",
        reading_datetime=datetime(2026, 6, 1, tzinfo=UTC),
        total_volume_m3=Decimal("50"),
        total_rob_m3=Decimal("50"),
        remarks=None,
        source="api",
        compartments=[],
    )
    await fs._upsert_reading(
        db,
        vessel_id=v2.id,
        action_type="received",
        product_name="Diesel DMA",
        reading_datetime=datetime(2026, 6, 2, tzinfo=UTC),
        total_volume_m3=Decimal("40"),
        total_rob_m3=Decimal("80"),
        remarks=None,
        source="xlsx_import",
        compartments=[],
    )

    resp_all = await mrv_flgo_index(
        FakeRequest(),
        vessel_id=None,
        action_type=None,
        source=None,
        date_from=None,
        date_to=None,
        db=db,
        user=_mrv_viewer_user(),
    )
    assert len(resp_all.context["rows"]) == 2

    resp_v1 = await mrv_flgo_index(
        FakeRequest(),
        vessel_id=v1.id,
        action_type=None,
        source=None,
        date_from=None,
        date_to=None,
        db=db,
        user=_mrv_viewer_user(),
    )
    assert len(resp_v1.context["rows"]) == 1
    assert resp_v1.context["rows"][0]["vessel"].code == "ANE"

    resp_received = await mrv_flgo_index(
        FakeRequest(),
        vessel_id=None,
        action_type="received",
        source=None,
        date_from=None,
        date_to=None,
        db=db,
        user=_mrv_viewer_user(),
    )
    assert len(resp_received.context["rows"]) == 1
    assert resp_received.context["rows"][0]["reading"].product_name == "Diesel DMA"

    resp_xlsx_source = await mrv_flgo_index(
        FakeRequest(),
        vessel_id=None,
        action_type=None,
        source="xlsx_import",
        date_from=None,
        date_to=None,
        db=db,
        user=_mrv_viewer_user(),
    )
    assert len(resp_xlsx_source.context["rows"]) == 1


@pytest.mark.asyncio
async def test_mrv_flgo_index_shows_internal_consistency_indicator(db):
    """R25 — l'écran calcule et affiche l'indicateur de cohérence interne
    (Σ compartiments vs total déclaré) sans jamais corriger la donnée."""
    from app.routers.mrv_router import mrv_flgo_index

    v = await _vessel(db)
    await fs._upsert_reading(
        db,
        vessel_id=v.id,
        action_type="measurement",
        product_name="Diesel Oil",
        reading_datetime=datetime(2026, 6, 1, tzinfo=UTC),
        total_volume_m3=Decimal("30"),
        total_rob_m3=Decimal("30"),
        remarks=None,
        source="api",
        compartments=[
            fs.CompartmentInput("14 - GO DB B", Decimal("15"), None),
            fs.CompartmentInput("15 - GO DB T", Decimal("10"), None),
        ],  # Σ=25 vs déclaré 30 → écart 5 > tolérance 2 m3
    )

    resp = await mrv_flgo_index(
        FakeRequest(),
        vessel_id=None,
        action_type=None,
        source=None,
        date_from=None,
        date_to=None,
        db=db,
        user=_mrv_viewer_user(),
    )
    assert len(resp.context["rows"]) == 1
    check = resp.context["rows"][0]["check"]
    assert check.flagged is True
    assert check.delta_m3 == Decimal("5")
    # Jamais corrigé : le total déclaré en base reste 30.
    reading = (await db.execute(select(FlgoReading))).scalar_one()
    assert reading.total_volume_m3 == Decimal("30")


# ═══════════════════════════════════════════════════ import xlsx bout-en-bout


@pytest.mark.asyncio
async def test_mrv_flgo_import_end_to_end_traced(db):
    from app.routers.mrv_router import mrv_flgo_import

    v = await _vessel(db)
    content = _build_xlsx(
        [
            [
                None,
                None,
                "Measurement",
                "06/07/2026 22:13",
                "14.6 m3 (12.76 t)",
                "16.4 m3 (14.33 t)",
                "31",
                "31",
                "",
                "0",
            ],
        ]
    )
    user = _mrv_editor_user()
    resp = await mrv_flgo_import(
        FakeRequest(),
        vessel_id=v.id,
        file=_upload(content),
        db=db,
        user=user,
    )
    assert resp.status_code == 200
    report = resp.context["report"]
    assert report.imported == 1
    assert report.updated == 0
    assert report.errors == []

    reading = (await db.execute(select(FlgoReading))).scalar_one()
    assert reading.vessel_id == v.id
    assert reading.source == "xlsx_import"

    log = (
        await db.execute(select(ActivityLog).where(ActivityLog.entity_type == "flgo_reading"))
    ).scalar_one()
    assert log.action == "import"
    assert log.user_id == user.id
    assert log.module == "mrv"


@pytest.mark.asyncio
async def test_mrv_flgo_import_reports_malformed_cell_without_crashing(db):
    """Cellule composite illisible → listée dans le rapport, pas d'exception."""
    from app.routers.mrv_router import mrv_flgo_import

    v = await _vessel(db)
    content = _build_xlsx(
        [
            [
                None,
                None,
                "Measurement",
                "06/07/2026 22:13",
                "14.6 m3 (12.76 t)",
                "garbage",
                "31",
                "31",
                "",
                "0",
            ],
        ]
    )
    resp = await mrv_flgo_import(
        FakeRequest(),
        vessel_id=v.id,
        file=_upload(content),
        db=db,
        user=_mrv_editor_user(),
    )
    assert resp.status_code == 200
    report = resp.context["report"]
    assert report.imported == 1
    assert len(report.errors) == 1


@pytest.mark.asyncio
async def test_mrv_flgo_import_unknown_vessel_404(db):
    from app.routers.mrv_router import mrv_flgo_import

    content = _build_xlsx([])
    with pytest.raises(HTTPException) as exc:
        await mrv_flgo_import(
            FakeRequest(),
            vessel_id=999999,
            file=_upload(content),
            db=db,
            user=_mrv_editor_user(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_mrv_flgo_import_rejects_disallowed_extension(db):
    from app.routers.mrv_router import mrv_flgo_import

    v = await _vessel(db)
    with pytest.raises(HTTPException) as exc:
        await mrv_flgo_import(
            FakeRequest(),
            vessel_id=v.id,
            file=_upload(b"plain text content", name="notes.txt"),
            db=db,
            user=_mrv_editor_user(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_mrv_flgo_import_idempotent_via_screen(db):
    """Ré-import du même fichier via l'écran → 0 doublon (upsert idempotent)."""
    from app.routers.mrv_router import mrv_flgo_import

    v = await _vessel(db)
    content = _build_xlsx(
        [
            [
                None,
                None,
                "Measurement",
                "06/07/2026 22:13",
                "14.6 m3 (12.76 t)",
                "16.4 m3 (14.33 t)",
                "31",
                "31",
                "",
                "0",
            ],
        ]
    )
    r1 = await mrv_flgo_import(
        FakeRequest(),
        vessel_id=v.id,
        file=_upload(content),
        db=db,
        user=_mrv_editor_user(),
    )
    assert r1.context["report"].imported == 1

    r2 = await mrv_flgo_import(
        FakeRequest(),
        vessel_id=v.id,
        file=_upload(content),
        db=db,
        user=_mrv_editor_user(),
    )
    assert (r2.context["report"].imported, r2.context["report"].updated) == (0, 1)

    readings = (await db.execute(select(FlgoReading))).scalars().all()
    assert len(readings) == 1
