"""Intégration — P8 : carnet éditorial (flux RSS, rubriques, gabarit illustré).

Couvre : les routes RSS (/carnet/rss.xml, /actualites/rss.xml) et leur type
MIME, le filtre par rubrique, l'ordre des routes (le flux RSS ne doit pas être
capturé par /carnet/{slug}), et le rendu de couverture/rubrique.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services import blog as blog_svc


def _req(query: dict | None = None, path: str = "/carnet"):
    return SimpleNamespace(
        headers={},
        cookies={},
        query_params=query or {},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(lang="fr"),
    )


async def _seed(db) -> None:
    from app.models.blog_post import BlogPost

    db.add_all(
        [
            BlogPost(
                slug="atlantis-essais",
                category="carnet",
                topic="chantier",
                title="Atlantis en essais",
                lead="Jalon chantier.",
                body="<p>Corps.</p>",
                cover_image="img/Artemis_devant.jpg",
                is_published=True,
                published_at=datetime(2026, 6, 12, tzinfo=UTC),
            ),
            BlogPost(
                slug="bordees-recrutement",
                category="carnet",
                topic="equipage",
                title="Les bordées s'étoffent",
                lead="Recrutement.",
                body="<p>Corps.</p>",
                is_published=True,
                published_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
            BlogPost(
                slug="cafe-arrive",
                category="actualite",
                topic="arrivees",
                title="Le café arrive à la voile",
                lead="Arrivée.",
                body="<p>Corps.</p>",
                is_published=True,
                published_at=datetime(2026, 2, 10, tzinfo=UTC),
            ),
        ]
    )
    await db.flush()


# ───────────────────── flux RSS ─────────────────────


@pytest.mark.asyncio
async def test_carnet_rss_route(db):
    from app.routers.vitrine_router import carnet_rss

    await _seed(db)
    resp = await carnet_rss(_req(path="/carnet/rss.xml"), db=db)
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.media_type
    body = resp.body.decode()
    assert "Atlantis en essais" in body
    assert "/carnet/atlantis-essais" in body
    # Ne mélange pas les sections : un billet actualité n'est pas dans le flux carnet.
    assert "Le café arrive" not in body


@pytest.mark.asyncio
async def test_actualites_rss_route(db):
    from app.routers.vitrine_router import actualites_rss

    await _seed(db)
    resp = await actualites_rss(_req(path="/actualites/rss.xml"), db=db)
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.media_type
    body = resp.body.decode()
    assert "Le café arrive à la voile" in body


def test_rss_route_precedes_slug_route():
    """Anti-régression : /carnet/rss.xml est déclarée AVANT /carnet/{slug}."""
    from app.routers import vitrine_router as mod

    paths = [getattr(r, "path", "") for r in mod.router.routes]
    assert "/carnet/rss.xml" in paths and "/carnet/{slug}" in paths
    assert paths.index("/carnet/rss.xml") < paths.index("/carnet/{slug}")


# ───────────────────── filtre par rubrique ─────────────────────


@pytest.mark.asyncio
async def test_carnet_topic_filter(db):
    from app.routers.vitrine_router import carnet_index

    await _seed(db)
    resp = await carnet_index(
        _req(query={"topic": "chantier"}, path="/carnet"), db=db, topic="chantier"
    )
    body = resp.body.decode()
    assert "Atlantis en essais" in body
    assert "Les bordées" not in body  # rubrique equipage exclue


@pytest.mark.asyncio
async def test_carnet_invalid_topic_shows_all(db):
    from app.routers.vitrine_router import carnet_index

    await _seed(db)
    resp = await carnet_index(_req(query={"topic": "vin"}, path="/carnet"), db=db, topic="vin")
    body = resp.body.decode()
    # Rubrique inconnue ignorée → les deux billets carnet sont listés.
    assert "Atlantis en essais" in body
    assert "Les bordées" in body


@pytest.mark.asyncio
async def test_service_topic_filter(db):
    await _seed(db)
    chantier = await blog_svc.list_published(db, category="carnet", topic="chantier")
    assert [p.slug for p in chantier] == ["atlantis-essais"]
    allc = await blog_svc.list_published(db, category="carnet")
    assert {p.slug for p in allc} == {"atlantis-essais", "bordees-recrutement"}
