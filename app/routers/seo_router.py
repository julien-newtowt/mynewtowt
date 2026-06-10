"""Artefacts SEO / lisibilité IA : robots.txt, llms.txt, sitemap.xml.

Servis à la racine du domaine. Contenu généré par ``app.services.seo``
(fonctions pures, testées). Base d'URL : ``settings.site_url``.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from app.config import settings
from app.services import seo

router = APIRouter(tags=["seo"])


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt() -> str:
    return seo.build_robots_txt(settings.site_url)


@router.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt() -> str:
    return seo.build_llms_txt(settings.site_url)


@router.get("/sitemap.xml")
async def sitemap_xml() -> Response:
    xml = seo.build_sitemap_xml(settings.site_url)
    return Response(content=xml, media_type="application/xml")
