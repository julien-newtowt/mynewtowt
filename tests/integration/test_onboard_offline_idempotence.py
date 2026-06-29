"""EVO-05 — PWA offline : idempotence serveur du rejeu de la file.

Le service worker (Background Sync) et le rejeu page rejouent les POST de la
file offline ; le serveur doit dédoublonner via ``client_uuid`` (un même UUID
ne crée qu'une seule entrée), sinon une saisie hors-ligne serait dupliquée.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.noon_report import NoonReport
from tests.integration.conftest import FakeRequest
from tests.integration.test_mrv_reprise import _setup_leg


def _noon_form(leg_id, client_uuid):
    return {
        "leg_id": str(leg_id),
        "latitude": "48.5",
        "longitude": "-5.1",
        "client_uuid": client_uuid,
    }


@pytest.mark.asyncio
async def test_noon_report_replay_is_idempotent(db, staff_user):
    from app.routers.onboard_router import post_noon_report

    leg = await _setup_leg(db)
    uuid = "11111111-2222-3333-4444-555555555555"

    # Premier POST (saisie) puis rejeu offline du même client_uuid.
    r1 = await post_noon_report(FakeRequest(_noon_form(leg.id, uuid)), db=db, user=staff_user)
    r2 = await post_noon_report(FakeRequest(_noon_form(leg.id, uuid)), db=db, user=staff_user)

    assert r1.status_code == 303
    assert r2.status_code == 303
    count = (
        await db.execute(
            select(func.count()).select_from(NoonReport).where(NoonReport.client_uuid == uuid)
        )
    ).scalar_one()
    assert count == 1  # le rejeu n'a pas dupliqué


@pytest.mark.asyncio
async def test_distinct_uuids_create_distinct_rows(db, staff_user):
    from app.routers.onboard_router import post_noon_report

    leg = await _setup_leg(db)
    await post_noon_report(FakeRequest(_noon_form(leg.id, "uuid-aaa")), db=db, user=staff_user)
    await post_noon_report(FakeRequest(_noon_form(leg.id, "uuid-bbb")), db=db, user=staff_user)

    total = (
        await db.execute(select(func.count()).select_from(NoonReport).where(NoonReport.leg_id == leg.id))
    ).scalar_one()
    assert total == 2


def test_sw_and_idb_assets_reference_sync():
    """Le service worker importe la file IndexedDB et gère l'événement sync."""
    from pathlib import Path

    static = Path(__file__).resolve().parents[2] / "app" / "static"
    sw = (static / "sw.js").read_text(encoding="utf-8")
    assert 'importScripts("/static/js/onboard-idb.js")' in sw
    assert "towt-onboard-flush" in sw
    assert 'addEventListener("sync"' in sw

    offline = (static / "js" / "onboard-offline.js").read_text(encoding="utf-8")
    assert "towtIdb" in offline
    assert "reg.sync.register" in offline

    idb = (static / "js" / "onboard-idb.js").read_text(encoding="utf-8")
    assert "indexedDB" in idb
    assert "towtIdb" in idb
