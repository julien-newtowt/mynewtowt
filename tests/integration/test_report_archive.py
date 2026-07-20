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

    report = await report_archive.save_report(
        db,
        pdf_bytes=b"content",
        report_type="trombinoscope",
        period="2026-08",
        generated_by=42,
    )
    assert report.generated_by == 42


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
