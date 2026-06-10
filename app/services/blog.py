"""Service du carnet de construction / actualités (lecture publique).

``slugify`` est pur (testable). Les accès en base ne renvoient que les
billets publiés, triés du plus récent au plus ancien.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blog_post import BlogPost

CATEGORIES = ("carnet", "actualite")


def slugify(value: str) -> str:
    """Translittère + normalise en slug URL : « Atlantis entre en essais »
    → « atlantis-entre-en-essais »."""
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "billet"


async def list_published(
    db: AsyncSession, *, category: str | None = None, limit: int = 50
) -> list[BlogPost]:
    stmt = select(BlogPost).where(BlogPost.is_published.is_(True))
    if category:
        stmt = stmt.where(BlogPost.category == category)
    stmt = stmt.order_by(
        BlogPost.published_at.desc().nullslast(), BlogPost.id.desc()
    ).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_published_by_slug(db: AsyncSession, slug: str) -> BlogPost | None:
    stmt = (
        select(BlogPost)
        .where(BlogPost.slug == slug)
        .where(BlogPost.is_published.is_(True))
    )
    return (await db.execute(stmt)).scalar_one_or_none()
