"""Onboard / Captain P0 — reprise (ONB-01) : édition/suppression SOF non signé."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.leg import Leg
from app.models.mrv import MRVEvent
from app.models.port import Port
from app.models.sof_event import SofEvent
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(
        id=1,
        leg_code="1CFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    db.add(leg)
    await db.flush()
    return leg


@pytest.mark.asyncio
async def test_edit_unsigned_sof_updates_and_resyncs_mrv(db, staff_user):
    from app.routers.captain_router import add_sof_event, edit_sof_event

    await _setup_leg(db)
    # SOSP (départ) → génère un MRVEvent dérivé.
    await add_sof_event(
        1,
        _Req(),
        event_type="SOSP",
        occurred_at="2026-04-01T08:00:00",
        label="Départ",
        latitude=None,
        longitude=None,
        notes=None,
        db=db,
        user=staff_user,
    )
    sof = (await db.execute(SofEvent.__table__.select())).fetchone()
    mrv = (await db.execute(MRVEvent.__table__.select())).fetchone()
    assert mrv is not None and mrv.event_kind == "departure"

    # Correction de l'heure → le MRVEvent dérivé suit.
    resp = await edit_sof_event(
        sof.id,
        _Req(),
        event_type="SOSP",
        occurred_at="2026-04-01T09:30:00",
        label="Départ corrigé",
        latitude=49.5,
        longitude=0.1,
        notes="recalé",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    refreshed = await db.get(SofEvent, sof.id)
    assert refreshed.occurred_at.hour == 9 and refreshed.label == "Départ corrigé"
    mrv2 = await db.get(MRVEvent, mrv.id)
    assert mrv2.recorded_at.hour == 9  # recordé_at réaligné


@pytest.mark.asyncio
async def test_edit_signed_sof_rejected(db, staff_user):
    from fastapi import HTTPException

    from app.routers.captain_router import edit_sof_event

    await _setup_leg(db)
    e = SofEvent(
        leg_id=1,
        event_type="NOR",
        occurred_at=datetime(2026, 4, 1, 10, tzinfo=UTC),
        is_locked=True,
        signed_by_name="Cmdt",
    )
    db.add(e)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await edit_sof_event(
            e.id,
            _Req(),
            event_type="NOR",
            occurred_at="2026-04-01T11:00:00",
            label="x",
            latitude=None,
            longitude=None,
            notes=None,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_unsigned_sof_cleans_mrv(db, staff_user):
    from app.routers.captain_router import add_sof_event, delete_sof_event

    await _setup_leg(db)
    await add_sof_event(
        1,
        _Req(),
        event_type="EOSP",
        occurred_at="2026-04-20T07:00:00",
        label=None,
        latitude=None,
        longitude=None,
        notes=None,
        db=db,
        user=staff_user,
    )
    sof = (await db.execute(SofEvent.__table__.select())).fetchone()
    assert (await db.execute(MRVEvent.__table__.select())).fetchone() is not None

    resp = await delete_sof_event(sof.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(SofEvent, sof.id)) is None
    # Le MRVEvent dérivé est nettoyé.
    assert (await db.execute(MRVEvent.__table__.select())).fetchone() is None


@pytest.mark.asyncio
async def test_delete_signed_sof_rejected(db, staff_user):
    from fastapi import HTTPException

    from app.routers.captain_router import delete_sof_event

    await _setup_leg(db)
    e = SofEvent(
        leg_id=1, event_type="NOR", occurred_at=datetime(2026, 4, 1, 10, tzinfo=UTC), is_locked=True
    )
    db.add(e)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await delete_sof_event(e.id, _Req(), db=db, user=staff_user)
    assert exc.value.status_code == 409
    assert (await db.get(SofEvent, e.id)) is not None


# ─────────────────────────────── ONB-03 ───────────────────────────────


class _Upload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


@pytest.fixture
def _upload_root(tmp_path, monkeypatch):
    """Redirige le stockage des fichiers vers un répertoire temporaire."""
    import app.services.safe_files as sf

    monkeypatch.setattr(sf, "_upload_root", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_leg_attachment_upload_download_delete(db, staff_user, _upload_root):
    from app.models.leg_attachment import LegAttachment
    from app.routers.captain_router import (
        delete_leg_attachment,
        download_leg_attachment,
        upload_leg_attachment,
    )

    await _setup_leg(db)
    pdf = b"%PDF-1.4 faux connaissement signe"
    resp = await upload_leg_attachment(
        1,
        _Req(),
        file=_Upload("bl_signe.pdf", pdf),
        category="bl_signed",
        label="BL #1",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    att = (await db.execute(LegAttachment.__table__.select())).fetchone()
    assert att is not None
    assert att.category == "bl_signed"
    assert att.file_mime == "application/pdf"
    assert (_upload_root / att.file_path).is_file()

    # Téléchargement → FileResponse pointant sur le fichier stocké.
    dl = await download_leg_attachment(1, att.id, db=db, user=staff_user)
    assert dl.status_code == 200
    assert dl.filename == "bl_signe.pdf"

    # Suppression → ligne + fichier disque retirés.
    stored = _upload_root / att.file_path
    resp = await delete_leg_attachment(1, att.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.execute(LegAttachment.__table__.select())).fetchone() is None
    assert not stored.exists()


@pytest.mark.asyncio
async def test_leg_attachment_rejects_bad_extension(db, staff_user, _upload_root):
    from fastapi import HTTPException

    from app.routers.captain_router import upload_leg_attachment

    await _setup_leg(db)
    with pytest.raises(HTTPException) as exc:
        await upload_leg_attachment(
            1,
            _Req(),
            file=_Upload("malware.exe", b"MZ\x90\x00"),
            category="other",
            label=None,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_leg_attachment_rejects_unknown_category(db, staff_user, _upload_root):
    from fastapi import HTTPException

    from app.routers.captain_router import upload_leg_attachment

    await _setup_leg(db)
    with pytest.raises(HTTPException) as exc:
        await upload_leg_attachment(
            1,
            _Req(),
            file=_Upload("doc.pdf", b"%PDF-1.4"),
            category="nope",
            label=None,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400
