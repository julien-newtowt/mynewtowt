"""ADM-08 — viewer d'audit : filtre par utilisateur + pagination.

Vérifie la pagination (``limit``/``page`` avec détection de page suivante) et le
filtre acteur (recherche partielle insensible à la casse sur ``user_name``).
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import FakeRequest


@pytest.mark.asyncio
async def test_pagination_and_actor_filter(db, staff_user):
    from app.routers.admin_router import activity_logs_view
    from app.services.activity import record

    for i in range(3):
        await record(
            db,
            action="create",
            user_name="alice" if i < 2 else "bob",
            module="cargo",
        )
    await db.flush()

    # Page 1, limit 2 → 2 lignes, page suivante détectée.
    resp = await activity_logs_view(FakeRequest(), limit=2, page=1, db=db, user=staff_user)
    assert resp.context["page"] == 1
    assert len(resp.context["logs"]) == 2
    assert resp.context["has_next"] is True
    assert resp.context["has_prev"] is False

    # Page 2 → 1 ligne restante, pas de page suivante.
    resp2 = await activity_logs_view(FakeRequest(), limit=2, page=2, db=db, user=staff_user)
    assert len(resp2.context["logs"]) == 1
    assert resp2.context["has_next"] is False
    assert resp2.context["has_prev"] is True

    # Filtre acteur (partiel, insensible à la casse).
    resp3 = await activity_logs_view(FakeRequest(), actor="ALI", db=db, user=staff_user)
    assert len(resp3.context["logs"]) == 2
    assert all(log.user_name == "alice" for log in resp3.context["logs"])


def test_template_has_actor_filter_and_pagination():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/admin/activity_logs.html")[0]
    assert 'name="actor"' in src
    assert "Précédent" in src
    assert "Suivant" in src
