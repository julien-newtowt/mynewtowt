"""Intégration — P6 « i18n stratégique (Brésil d'abord) ».

Couvre :
- les 3 pages piliers converties aux clés i18n (impact, preuves,
  solutions/cafe) servies en PT-BR/ES sans bandeau « traduction en cours » ;
- le retrait des bandeaux obsolètes sur les pages déjà traduites par clés
  (flotte, recrutement) ;
- l'honnêteté hreflang : pages FR-only sans alternates (layout + sitemap),
  pages fr/en limitées à fr+en, pages traduites déclarées ×4 ;
- la parité de clés des 5 catalogues (garde générale, au-delà de fr↔vi).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.seo import build_sitemap_xml


def _req(lang: str = "fr", path: str = "/") -> SimpleNamespace:
    return SimpleNamespace(
        headers={},
        cookies={},
        query_params={},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(lang=lang, forced_lang=lang),
    )


# ───────────────────── pages piliers traduites ─────────────────────


@pytest.mark.asyncio
async def test_impact_served_in_ptbr_without_notice():
    from app.routers.vitrine_router import impact

    resp = await impact(_req("pt-br", "/impact"))
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "Transportar à vela também é transportar melhor" in body
    assert "Tradução em andamento" not in body


@pytest.mark.asyncio
async def test_impact_served_in_es():
    from app.routers.vitrine_router import impact

    resp = await impact(_req("es", "/impact"))
    body = resp.body.decode()
    assert "Transportar a vela también es transportar mejor" in body
    assert "Traducción en curso" not in body


@pytest.mark.asyncio
async def test_preuves_served_in_ptbr_without_notice():
    from app.routers.vitrine_router import preuves

    resp = await preuves(_req("pt-br", "/preuves"))
    body = resp.body.decode()
    assert "A prova, não apenas a promessa." in body
    assert "Baixar a metodologia (PDF)" in body
    assert "Tradução em andamento" not in body


@pytest.mark.asyncio
async def test_solutions_cafe_served_in_ptbr_without_notice():
    from app.routers.public_router import solutions_cafe

    resp = await solutions_cafe(_req("pt-br", "/solutions/cafe"))
    body = resp.body.decode()
    assert "O café verde, atravessado à vela." in body
    assert "Tradução em andamento" not in body


@pytest.mark.asyncio
async def test_preuves_french_copy_unchanged():
    """La conversion aux clés n'a pas altéré la copie FR de référence."""
    from app.routers.vitrine_router import preuves

    resp = await preuves(_req("fr", "/preuves"))
    body = resp.body.decode()
    assert "La preuve, pas seulement la promesse." in body
    assert "Télécharger la méthodologie (PDF)" in body
    assert "la vérification tierce porte sur" in body


# ───────────────────── bandeaux obsolètes retirés ─────────────────────


@pytest.mark.asyncio
async def test_keyed_pages_lost_their_stale_notice():
    from app.routers.vitrine_router import fleet_capabilities, recrutement

    for handler, path in ((fleet_capabilities, "/flotte"), (recrutement, "/recrutement")):
        resp = await handler(_req("es", path))
        body = resp.body.decode()
        assert "Traducción en curso" not in body, path


# ───────────────────── hreflang honnête (layout) ─────────────────────


@pytest.mark.asyncio
async def test_fr_only_page_declares_no_alternates():
    from app.routers.vitrine_router import navigation

    resp = await navigation(_req("pt-br", "/navigation"))
    body = resp.body.decode()
    # Toujours FR + bandeau (page non traduite), et AUCUN alternate SEO
    # déclaré (le sélecteur de langue du header, lui, reste — attributs
    # hreflang sur <a>, pas des <link rel="alternate">).
    assert "Tradução em andamento" in body
    assert '<link rel="alternate"' not in body


@pytest.mark.asyncio
async def test_translated_page_declares_four_alternates():
    from app.routers.vitrine_router import impact

    resp = await impact(_req("fr", "/impact"))
    body = resp.body.decode()
    for code in ("fr", "en", "es", "pt-BR", "x-default"):
        assert f'<link rel="alternate" hreflang="{code}"' in body


@pytest.mark.asyncio
async def test_fr_en_page_declares_two_alternates(db):
    from app.routers.vitrine_router import verify_certificate

    resp = await verify_certificate(_req("fr", "/verify"), ref=None, db=db)
    body = resp.body.decode()
    assert '<link rel="alternate" hreflang="fr"' in body
    assert '<link rel="alternate" hreflang="en"' in body
    assert '<link rel="alternate" hreflang="pt-BR"' not in body
    assert '<link rel="alternate" hreflang="es"' not in body


# ───────────────────── sitemap honnête ─────────────────────


def test_sitemap_alternates_match_served_languages():
    xml = build_sitemap_xml("https://newtowt.eu")
    blocks = {
        b[b.find("<loc>") + 5 : b.find("</loc>")]: b for b in xml.split("<url>") if "<loc>" in b
    }
    # Page traduite ×4 → alternates complets.
    assert 'hreflang="pt-BR"' in blocks["https://newtowt.eu/impact"]
    assert 'hreflang="pt-BR"' in blocks["https://newtowt.eu/solutions/cafe"]
    # Page FR-only → aucun alternate.
    assert "hreflang" not in blocks["https://newtowt.eu/navigation"]
    assert "hreflang" not in blocks["https://newtowt.eu/presse"]
    # Page fr/en → exactement fr + en + x-default.
    about = blocks["https://newtowt.eu/about"]
    assert 'hreflang="fr"' in about and 'hreflang="en"' in about
    assert 'hreflang="es"' not in about and 'hreflang="pt-BR"' not in about


# ───────────────────── parité générale des catalogues ─────────────────────


def test_all_five_catalogs_share_the_same_keys():
    """Garde générale : plus large que la parité fr↔vi historique (UX-02)."""
    import importlib

    fr = set(importlib.import_module("app.i18n.fr").CATALOG)
    for lang in ("en", "es", "pt_br", "vi"):
        cat = set(importlib.import_module(f"app.i18n.{lang}").CATALOG)
        assert cat == fr, f"écart de clés {lang}↔fr : {sorted(cat ^ fr)[:10]}"
