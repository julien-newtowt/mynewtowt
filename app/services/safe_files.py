"""Stockage sûr des fichiers uploadés.

Valide (extension + taille + magic number via ``utils.file_validation``),
génère un nom de fichier aléatoire (pas de nom client en clair sur le
disque) et écrit sous ``settings.upload_dir/<subdir>/``. La lecture passe
par ``resolve_path`` qui garantit l'absence de path-traversal.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from app.config import settings
from app.utils.file_validation import MAX_FILE_SIZE_MB, validate_upload


class UploadRejected(Exception):
    """Upload refusé (extension/taille/MIME invalide ou chemin suspect)."""


# Marge multipart : Content-Length couvre le fichier + champs de formulaire +
# délimiteurs ; on tolère 1 Mo de surcoût avant de rejeter en amont (le contrôle
# fin de taille du fichier reste assuré par ``validate_size`` après lecture).
_MAX_REQUEST_BYTES = (MAX_FILE_SIZE_MB + 1) * 1024 * 1024


def content_length_exceeds_max(content_length: str | None) -> bool:
    """True si l'en-tête Content-Length dépasse la taille max tolérée.

    Sert de pré-filtre anti-OOM : permet de rejeter (413) un upload géant
    AVANT de charger le corps de la requête en mémoire.
    """
    if content_length and content_length.isdigit():
        return int(content_length) > _MAX_REQUEST_BYTES
    return False


def _upload_root() -> Path:
    root = Path(settings.upload_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root


def save_upload(content: bytes, original_name: str, *, subdir: str) -> tuple[str, str | None]:
    """Valide puis écrit le fichier. Renvoie (chemin_relatif, mime_détecté).

    Lève ``UploadRejected`` si la validation échoue.
    """
    res = validate_upload(original_name, content)
    if not res.ok:
        raise UploadRejected(res.reason or "fichier rejeté")
    ext = Path(original_name).suffix.lower()
    safe_subdir = subdir.strip("/").replace("..", "")
    rel_path = f"{safe_subdir}/{secrets.token_hex(16)}{ext}"
    dest = _upload_root() / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return rel_path, res.detected_mime


def resolve_path(rel_path: str) -> Path:
    """Résout un chemin relatif stocké en chemin absolu, anti-traversal."""
    root = _upload_root().resolve()
    candidate = (root / rel_path).resolve()
    if candidate != root and not str(candidate).startswith(str(root) + os.sep):
        raise UploadRejected("chemin invalide")
    if not candidate.is_file():
        raise FileNotFoundError(rel_path)
    return candidate
