"""Intégration — notification in-app à la génération du trombinoscope.

Cf. docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md (module TRB-5).
"""

from __future__ import annotations

import pytest

from app.services import notifications


@pytest.mark.asyncio
async def test_notify_trombinoscope_generated_targets_armement_role(db):
    n = await notifications.notify_trombinoscope_generated(db, period="2026-07")

    assert n.type == "trombinoscope_generated"
    assert n.target_role == "armement"
    assert n.target_user_id is None
    # Lien vers la route de téléchargement en lecture seule (GET, review
    # 2026-07-20) — pas vers la régénération manuelle (POST, casserait en 405).
    assert n.link == "/crew/trombinoscope/latest.pdf"
    assert "2026-07" in n.title


@pytest.mark.asyncio
async def test_notify_trombinoscope_generated_visible_to_armement_role(db):
    await notifications.notify_trombinoscope_generated(db, period="2026-08")

    rows = await notifications.list_for(db, user_role="armement")
    assert any(r.type == "trombinoscope_generated" for r in rows)


@pytest.mark.asyncio
async def test_manual_regeneration_does_not_notify(db, tmp_path, monkeypatch):
    """Review 2026-07-20 : seule la génération automatique mensuelle notifie
    l'Armement — un utilisateur qui régénère manuellement vient de cliquer,
    il n'a pas besoin d'être notifié de sa propre action. Nécessite WeasyPrint
    (non disponible en environnement de dev local sans le runtime GTK3 —
    exécuté par la CI)."""
    from app.routers.crew_router import crew_directory_pdf
    from app.services import report_archive
    from app.services.crew_directory import invalidate_cache

    monkeypatch.setattr(report_archive.settings, "upload_dir", str(tmp_path))
    invalidate_cache()

    class _User:
        id = 1

    await crew_directory_pdf(db=db, user=_User())

    rows = await notifications.list_for(db, user_role="armement")
    assert not any(r.type == "trombinoscope_generated" for r in rows)
