"""Tests for app.utils.file_validation."""
from __future__ import annotations

from app.utils.file_validation import (
    ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB,
    sniff_mime, validate_filename, validate_size, validate_upload,
)


def test_allowed_extensions_include_common_office():
    assert ".pdf" in ALLOWED_EXTENSIONS
    assert ".docx" in ALLOWED_EXTENSIONS
    assert ".xlsx" in ALLOWED_EXTENSIONS


def test_validate_filename_accepts_pdf():
    r = validate_filename("invoice.pdf")
    assert r.ok


def test_validate_filename_rejects_exe():
    r = validate_filename("evil.exe")
    assert not r.ok


def test_validate_filename_rejects_path_traversal():
    r = validate_filename("../../etc/passwd")
    assert not r.ok


def test_validate_filename_rejects_empty():
    assert not validate_filename("").ok


def test_validate_size_accepts_small_file():
    r = validate_size(b"x" * 100)
    assert r.ok


def test_validate_size_rejects_too_large():
    r = validate_size(b"x" * (MAX_FILE_SIZE_MB * 1024 * 1024 + 1))
    assert not r.ok


def test_sniff_mime_pdf():
    assert sniff_mime(b"%PDF-1.7\n..." + b"x" * 100) == "application/pdf"


def test_sniff_mime_png():
    assert sniff_mime(b"\x89PNG\r\n\x1a\n" + b"x" * 100) == "image/png"


def test_sniff_mime_jpeg():
    assert sniff_mime(b"\xff\xd8\xff\xe0...") == "image/jpeg"


def test_sniff_mime_zip_docx():
    # DOCX is actually a ZIP
    assert sniff_mime(b"PK\x03\x04..." + b"x" * 100) == "application/zip"


def test_sniff_mime_unknown_returns_none():
    assert sniff_mime(b"random bytes") is None


def test_validate_upload_happy_path():
    r = validate_upload("doc.pdf", b"%PDF-1.7\n" + b"x" * 100)
    assert r.ok
    assert r.detected_mime == "application/pdf"


def test_validate_upload_rejects_bad_extension():
    r = validate_upload("doc.exe", b"\x4d\x5a")
    assert not r.ok
