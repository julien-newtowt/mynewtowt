"""Service du carnet de construction / actualités (lecture publique).

``slugify`` et ``build_rss`` sont purs (testables). Les accès en base ne
renvoient que les billets publiés, triés du plus récent au plus ancien.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from xml.sax.saxutils import escape as _xml_escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blog_post import BlogPost

CATEGORIES = ("carnet", "actualite")

# Rubriques éditoriales (P8). Ordre = ordre d'affichage des filtres.
TOPICS: tuple[str, ...] = ("arrivees", "chantier", "equipage", "clients")
TOPIC_LABELS: dict[str, str] = {
    "arrivees": "Arrivées",
    "chantier": "Chantier",
    "equipage": "Équipage",
    "clients": "Clients",
}


def is_valid_topic(topic: str | None) -> bool:
    return bool(topic) and topic in TOPICS


def topic_label(topic: str | None) -> str:
    return TOPIC_LABELS.get(topic or "", "")


def slugify(value: str) -> str:
    """Translittère + normalise en slug URL : « Atlantis entre en essais »
    → « atlantis-entre-en-essais »."""
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "billet"


async def list_published(
    db: AsyncSession,
    *,
    category: str | None = None,
    topic: str | None = None,
    limit: int = 50,
) -> list[BlogPost]:
    stmt = select(BlogPost).where(BlogPost.is_published.is_(True))
    if category:
        stmt = stmt.where(BlogPost.category == category)
    if topic and topic in TOPICS:
        stmt = stmt.where(BlogPost.topic == topic)
    stmt = stmt.order_by(BlogPost.published_at.desc().nullslast(), BlogPost.id.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_published_by_slug(db: AsyncSession, slug: str) -> BlogPost | None:
    stmt = select(BlogPost).where(BlogPost.slug == slug).where(BlogPost.is_published.is_(True))
    return (await db.execute(stmt)).scalar_one_or_none()


def _rfc822(dt: datetime | None) -> str:
    """Date RFC-822 pour un flux RSS (UTC). Défaut : maintenant."""
    if dt is None:
        dt = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


def build_rss(
    posts: list[BlogPost],
    *,
    base_url: str,
    title: str,
    description: str,
    self_path: str,
) -> str:
    """Construit un flux RSS 2.0 valide (texte). Pur — testable sans DB.

    ``base_url`` sans slash final ; ``self_path`` = chemin du flux
    (ex. ``/carnet/rss.xml``). Les billets pointent vers ``/carnet/{slug}``.
    Tout contenu externe est échappé XML (jamais de HTML brut dans le flux).
    """
    base = base_url.rstrip("/")
    now = _rfc822(None)
    items: list[str] = []
    for p in posts:
        link = f"{base}/carnet/{p.slug}"
        desc = p.lead or (p.title or "")
        items.append(
            "    <item>\n"
            f"      <title>{_xml_escape(p.title or '')}</title>\n"
            f"      <link>{_xml_escape(link)}</link>\n"
            f'      <guid isPermaLink="true">{_xml_escape(link)}</guid>\n'
            f"      <pubDate>{_rfc822(p.published_at)}</pubDate>\n"
            f"      <description>{_xml_escape(desc)}</description>\n"
            "    </item>"
        )
    items_xml = "\n".join(items)
    self_link = f"{base}{self_path}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{_xml_escape(title)}</title>\n"
        f"    <link>{_xml_escape(base + '/carnet')}</link>\n"
        f"    <description>{_xml_escape(description)}</description>\n"
        "    <language>fr</language>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        f'    <atom:link href="{_xml_escape(self_link)}" rel="self" type="application/rss+xml"/>\n'
        f"{items_xml}\n"
        "  </channel>\n"
        "</rss>\n"
    )
