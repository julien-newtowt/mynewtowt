"""Unit tests — artefacts SEO / lisibilité IA (fonctions pures)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.services import seo

BASE = "https://newtowt.eu"


# ───────────────────────── robots.txt ─────────────────────────
def test_robots_allows_all_and_sitemap() -> None:
    txt = seo.build_robots_txt(BASE)
    assert "User-agent: *" in txt
    assert "Allow: /" in txt
    assert f"Sitemap: {BASE}/sitemap.xml" in txt


@pytest.mark.parametrize("bot", ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"])
def test_robots_explicitly_allows_ai_bots(bot: str) -> None:
    assert f"User-agent: {bot}" in seo.build_robots_txt(BASE)


def test_robots_disallows_private_areas() -> None:
    txt = seo.build_robots_txt(BASE)
    for path in ("/admin/", "/me/", "/booking/", "/api/"):
        assert f"Disallow: {path}" in txt


def test_robots_trailing_slash_normalized() -> None:
    assert "https://newtowt.eu//sitemap.xml" not in seo.build_robots_txt(BASE + "/")


# ───────────────────────── llms.txt ─────────────────────────
def test_llms_txt_structure_and_links() -> None:
    txt = seo.build_llms_txt(BASE)
    assert txt.startswith("# NewTowt")
    assert "> " in txt  # blockquote summary
    assert f"{BASE}/flotte" in txt
    assert f"{BASE}/impact" in txt
    assert "ANEMOS" in txt


# ───────────────────────── sitemap.xml ─────────────────────────
def test_sitemap_is_well_formed_xml() -> None:
    root = ET.fromstring(seo.build_sitemap_xml(BASE))
    assert root.tag.endswith("urlset")


def test_sitemap_contains_core_pages() -> None:
    xml = seo.build_sitemap_xml(BASE)
    assert f"<loc>{BASE}/</loc>" in xml
    assert f"<loc>{BASE}/flotte</loc>" in xml
    assert f"<loc>{BASE}/contact</loc>" in xml


def test_sitemap_has_hreflang_alternates() -> None:
    xml = seo.build_sitemap_xml(BASE)
    assert 'hreflang="pt-BR"' in xml
    assert 'hreflang="x-default"' in xml
    assert f'href="{BASE}/flotte?lang=es"' in xml


def test_sitemap_url_count_matches_pages() -> None:
    xml = seo.build_sitemap_xml(BASE)
    assert xml.count("<url>") == len(seo.PUBLIC_PAGES)


# ───────────────────────── JSON-LD ─────────────────────────
def test_organization_jsonld_facts() -> None:
    org = seo.organization_jsonld(BASE)
    assert org["@type"] == "Organization"
    assert org["name"] == "NewTowt"
    assert org["founder"]["name"] == "Karl Sement"
    assert org["foundingDate"] == "2011"
    assert "994 529 873" in org["taxID"]
    assert org["@id"] == f"{BASE}/#organization"


def test_service_jsonld_links_to_org() -> None:
    svc = seo.service_jsonld(BASE)
    assert svc["@type"] == "Service"
    assert svc["provider"]["@id"] == f"{BASE}/#organization"


def test_faq_jsonld_shape() -> None:
    faq = seo.faq_jsonld([("Q1?", "A1."), ("Q2?", "A2.")])
    assert faq["@type"] == "FAQPage"
    assert len(faq["mainEntity"]) == 2
    assert faq["mainEntity"][0]["acceptedAnswer"]["text"] == "A1."


def test_breadcrumb_jsonld_positions() -> None:
    bc = seo.breadcrumb_jsonld(BASE, [("Accueil", "/"), ("Flotte", "/flotte")])
    items = bc["itemListElement"]
    assert items[0]["position"] == 1
    assert items[1]["item"] == f"{BASE}/flotte"
