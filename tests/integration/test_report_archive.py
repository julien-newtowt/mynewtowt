"""Intégration — archivage des rapports générés (`services.report_archive`).

Couvre l'écriture du fichier + la ligne d'archive, et la lecture de la
dernière archive par type. Cf.
docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md §4.2.
"""

from __future__ import annotations

import pytest

from app.services import report_archive


@pytest.mark.asyncio
async def test_save_report_writes_file_and_row(db, tmp_path, monkeypatch):
    monkeypatch.setattr(report_archive.settings, "upload_dir", str(tmp_path))

    report = await report_archive.save_report(
        db,
        pdf_bytes=b"%PDF-1.4 fake content",
        report_type="trombinoscope",
        period="2026-07",
        generated_by=None,
    )

    assert report.id is not None
    assert report.type == "trombinoscope"
    assert report.period == "2026-07"
    assert report.generated_by is None

    from app.services.safe_files import resolve_path

    written = resolve_path(report.file_path)
    assert written.read_bytes() == b"%PDF-1.4 fake content"


@pytest.mark.asyncio
async def test_save_report_records_generated_by_user(db, tmp_path, monkeypatch):
    monkeypatch.setattr(report_archive.settings, "upload_dir", str(tmp_path))

    # generated_by est une vraie FK vers users.id (bug de test trouvé en
    # exécution réelle le 2026-07-21) : id=1 est l'utilisateur admin semé par
    # tests/integration/conftest.py — un id arbitraire (42) viole la contrainte.
    report = await report_archive.save_report(
        db,
        pdf_bytes=b"content",
        report_type="trombinoscope",
        period="2026-08",
        generated_by=1,
    )
    assert report.generated_by == 1


@pytest.mark.asyncio
async def test_latest_report_returns_most_recent_by_type(db, tmp_path, monkeypatch):
    monkeypatch.setattr(report_archive.settings, "upload_dir", str(tmp_path))

    first = await report_archive.save_report(
        db, pdf_bytes=b"1", report_type="trombinoscope", period="2026-06", generated_by=None
    )
    second = await report_archive.save_report(
        db, pdf_bytes=b"2", report_type="trombinoscope", period="2026-07", generated_by=None
    )

    latest = await report_archive.latest_report(db, report_type="trombinoscope")
    assert latest is not None
    assert latest.id == second.id
    assert latest.id != first.id


@pytest.mark.asyncio
async def test_latest_report_none_when_no_reports_of_type(db):
    latest = await report_archive.latest_report(db, report_type="unknown_type_xyz")
    assert latest is None


# ───────────── GET /crew/trombinoscope/latest.pdf (review 2026-07-20) ─────────────
# Route de lecture seule ajoutée pour que la notification pointe vers un
# téléchargement (GET, sans effet de bord) plutôt que vers la régénération
# manuelle (POST). Appelée directement (patron tests/integration/test_fleet_roster.py)
# plutôt que via TestClient — pas de DB dans le get_db surchargé sinon.


@pytest.mark.asyncio
async def test_latest_pdf_route_404_when_nothing_generated(db):
    from fastapi import HTTPException

    from app.routers.crew_router import crew_directory_latest_pdf

    with pytest.raises(HTTPException) as exc_info:
        await crew_directory_latest_pdf(db=db, user=None)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_latest_pdf_route_serves_most_recent_report(db, tmp_path, monkeypatch):
    monkeypatch.setattr(report_archive.settings, "upload_dir", str(tmp_path))
    from app.routers.crew_router import crew_directory_latest_pdf

    await report_archive.save_report(
        db, pdf_bytes=b"old", report_type="trombinoscope", period="2026-06", generated_by=None
    )
    await report_archive.save_report(
        db, pdf_bytes=b"new", report_type="trombinoscope", period="2026-07", generated_by=None
    )

    resp = await crew_directory_latest_pdf(db=db, user=None)
    assert resp.body == b"new"
    assert resp.media_type == "application/pdf"
    assert "2026-07" in resp.headers["content-disposition"]
