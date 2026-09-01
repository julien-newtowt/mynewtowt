"""Archivage des documents PDF générés serveur (trombinoscope, futurs rapports).

Contrairement à ``services.safe_files`` (contenu **uploadé par un
utilisateur** : validé par extension/taille/magic number), le contenu ici est
**généré par l'application elle-même** (WeasyPrint) — aucune validation
d'upload n'est nécessaire. On réutilise en revanche le même principe de
nommage aléatoire et le même ``settings.upload_dir``, afin que la lecture
passe par ``services.safe_files.resolve_path`` (anti path-traversal déjà
éprouvé) sans dupliquer cette logique.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.generated_report import GeneratedReport

_SUBDIR = "generated_reports"


def _upload_root() -> Path:
    root = Path(settings.upload_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    return root


async def save_report(
    db: AsyncSession,
    *,
    pdf_bytes: bytes,
    report_type: str,
    period: str,
    generated_by: int | None,
) -> GeneratedReport:
    """Écrit le PDF sur disque puis enregistre la ligne d'archive correspondante.

    ``await db.flush()`` est appelé ici (pas ``commit`` — laissé à la
    dépendance ``get_db()``), cohérent avec les conventions du projet.
    """
    rel_path = f"{_SUBDIR}/{secrets.token_hex(16)}.pdf"
    dest = _upload_root() / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf_bytes)

    report = GeneratedReport(
        type=report_type,
        period=period,
        file_path=rel_path,
        generated_by=generated_by,
    )
    db.add(report)
    await db.flush()
    return report


async def latest_report(db: AsyncSession, *, report_type: str) -> GeneratedReport | None:
    """Dernière archive générée pour ce type (tous mois confondus).

    Départage par ``id`` en plus de ``generated_at`` (bug trouvé en exécutant
    réellement les tests le 2026-07-21) : deux rapports créés dans la même
    transaction/seconde peuvent partager un ``generated_at`` identique
    (résolution à la seconde de `CURRENT_TIMESTAMP`), rendant le tri par
    date seule ambigu. ``id`` (auto-incrément) est un critère fiable et
    monotone pour "le plus récemment créé".
    """
    return (
        await db.execute(
            select(GeneratedReport)
            .where(GeneratedReport.type == report_type)
            .order_by(GeneratedReport.generated_at.desc(), GeneratedReport.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
