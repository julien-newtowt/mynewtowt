"""Validation des uploads — taille, MIME, extension.

Reprise V3.0.0. Approche : whitelist d'extensions + sniffing du magic
number sur les premiers octets (sans dépendre de `python-magic`).
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_FILE_SIZE_MB = 20
ALLOWED_EXTENSIONS: tuple[str, ...] = (
    ".pdf", ".docx", ".xlsx", ".xls", ".doc",
    ".png", ".jpg", ".jpeg", ".webp",
    ".csv", ".txt", ".zip",
)

# Magic numbers (premier octets) — détection rapide sans python-magic
_MAGIC_SIGNATURES: dict[bytes, str] = {
    b"%PDF-":       "application/pdf",
    b"PK\x03\x04":  "application/zip",  # also docx/xlsx/pptx
    b"\x89PNG\r\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF":        "image/webp",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "application/msword",  # DOC/XLS old
}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None = None
    detected_mime: str | None = None


def validate_filename(name: str) -> ValidationResult:
    if not name:
        return ValidationResult(False, "nom de fichier vide")
    lname = name.lower()
    if not any(lname.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        return ValidationResult(False, f"extension non autorisée ({name})")
    if "/" in name or "\\" in name or ".." in name:
        return ValidationResult(False, "chemin invalide dans le nom de fichier")
    return ValidationResult(True)


def validate_size(content: bytes, max_mb: int = MAX_FILE_SIZE_MB) -> ValidationResult:
    size_mb = len(content) / (1024 * 1024)
    if size_mb > max_mb:
        return ValidationResult(False, f"fichier trop volumineux ({size_mb:.1f} Mo > {max_mb} Mo)")
    return ValidationResult(True)


def sniff_mime(content: bytes) -> str | None:
    head = content[:16]
    for sig, mime in _MAGIC_SIGNATURES.items():
        if head.startswith(sig):
            return mime
    return None


def validate_upload(name: str, content: bytes, max_mb: int = MAX_FILE_SIZE_MB) -> ValidationResult:
    r = validate_filename(name)
    if not r.ok:
        return r
    r = validate_size(content, max_mb=max_mb)
    if not r.ok:
        return r
    mime = sniff_mime(content)
    return ValidationResult(True, detected_mime=mime)
