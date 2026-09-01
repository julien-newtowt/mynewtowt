"""Validation des uploads — taille, MIME, extension.

Reprise V3.0.0. Approche : whitelist d'extensions + sniffing du magic
number sur les premiers octets (sans dépendre de `python-magic`).
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_FILE_SIZE_MB = 20
ALLOWED_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".docx",
    ".xlsx",
    ".xls",
    ".doc",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    # Formats natifs des photos de téléphone. Sans eux, un justificatif
    # photographié depuis un iPhone (HEIC par défaut) ou un Android récent
    # (AVIF) était rejeté au motif « extension non autorisée » — alors que les
    # champs concernés sont explicitement des prises de vue (`capture` =
    # appareil photo) : justificatif de caisse, scan de BL, document équipage.
    ".heic",
    ".heif",
    ".avif",
    ".csv",
    ".txt",
    ".zip",
)

# Magic numbers (premier octets) — détection rapide sans python-magic
_MAGIC_SIGNATURES: dict[bytes, str] = {
    b"%PDF-": "application/pdf",
    b"PK\x03\x04": "application/zip",  # also docx/xlsx/pptx
    b"\x89PNG\r\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF": "image/webp",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "application/msword",  # DOC/XLS old
}

# Familles ISO-BMFF : la signature n'est pas en tête de fichier mais à
# l'offset 4 (`ftyp`), suivie d'une « marque » qui désigne le format exact.
_FTYP_BRANDS: dict[bytes, str] = {
    b"heic": "image/heic",
    b"heix": "image/heic",
    b"hevc": "image/heic",
    b"hevx": "image/heic",
    b"mif1": "image/heif",
    b"msf1": "image/heif",
    b"avif": "image/avif",
    b"avis": "image/avif",
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
        # Message actionnable : l'utilisateur doit savoir quoi faire, pas
        # seulement que c'est refusé.
        return ValidationResult(
            False,
            f"format non accepté ({name}). Formats acceptés : "
            "photo (JPG, PNG, HEIC, WEBP), PDF, Word, Excel, CSV, ZIP.",
        )
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
    # HEIC / HEIF / AVIF : conteneur ISO-BMFF, `ftyp` à l'offset 4.
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return _FTYP_BRANDS.get(head[8:12])
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
