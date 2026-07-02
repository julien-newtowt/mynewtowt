"""Unit tests — service carnet/blog (slugify + RSS + rubriques, purs)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.blog import (
    CATEGORIES,
    TOPIC_LABELS,
    TOPICS,
    build_rss,
    is_valid_topic,
    slugify,
    topic_label,
)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Atlantis entre en essais", "atlantis-entre-en-essais"),
        ("Quatre sisterships pour étendre la ligne", "quatre-sisterships-pour-etendre-la-ligne"),
        ("  Espaces  multiples  ", "espaces-multiples"),
        ("Accents: é à ç ô û", "accents-e-a-c-o-u"),
        ("Ponctuation !? «citée»", "ponctuation-citee"),
        ("MAJUSCULES", "majuscules"),
    ],
)
def test_slugify(title: str, expected: str) -> None:
    assert slugify(title) == expected


@pytest.mark.parametrize("bad", ["", "   ", "!!!", "—"])
def test_slugify_fallback(bad: str) -> None:
    assert slugify(bad) == "billet"


def test_categories_constant() -> None:
    assert "carnet" in CATEGORIES and "actualite" in CATEGORIES


# ───────────────────────── rubriques (P8) ─────────────────────────


def test_topics_and_labels() -> None:
    assert TOPICS == ("arrivees", "chantier", "equipage", "clients")
    assert set(TOPIC_LABELS) == set(TOPICS)
    assert topic_label("chantier") == "Chantier"
    assert topic_label(None) == "" and topic_label("inconnu") == ""


@pytest.mark.parametrize("ok", list(TOPICS))
def test_is_valid_topic_accepts_known(ok: str) -> None:
    assert is_valid_topic(ok)


@pytest.mark.parametrize("bad", ["", None, "vin", "Chantier"])
def test_is_valid_topic_rejects_unknown(bad) -> None:
    assert not is_valid_topic(bad)


# ───────────────────────── flux RSS (P8) ─────────────────────────


def _post(slug: str, title: str, lead: str | None, dt: datetime | None):
    return SimpleNamespace(slug=slug, title=title, lead=lead, published_at=dt)


def test_build_rss_is_well_formed_and_lists_items() -> None:
    import xml.etree.ElementTree as ET

    posts = [
        _post(
            "atlantis-essais",
            "Atlantis en essais",
            "Jalon.",
            datetime(2026, 6, 12, tzinfo=UTC),
        ),
        _post("cafe-arrive", "Le café arrive", None, None),
    ]
    xml = build_rss(
        posts,
        base_url="https://newtowt.eu/",
        title="NewTowt — Carnet",
        description="Le carnet.",
        self_path="/carnet/rss.xml",
    )
    # Doit parser (XML valide) et exposer 2 items sous <channel>.
    root = ET.fromstring(xml)
    channel = root.find("channel")
    items = channel.findall("item")
    assert len(items) == 2
    # Liens absolus vers /carnet/{slug}, base sans double slash.
    links = [it.find("link").text for it in items]
    assert "https://newtowt.eu/carnet/atlantis-essais" in links
    assert "https://newtowt.eu/carnet/cafe-arrive" in links
    # Lien self atom présent.
    assert 'href="https://newtowt.eu/carnet/rss.xml"' in xml
    assert 'type="application/rss+xml"' in xml


def test_build_rss_escapes_xml_special_chars() -> None:
    posts = [_post("s", "Titre & <balise>", 'Lead "citée" & co', None)]
    xml = build_rss(
        posts, base_url="https://x.eu", title="T & U", description="D", self_path="/carnet/rss.xml"
    )
    # Aucun chevron/esperluette brut injecté (échappement XML).
    assert "<balise>" not in xml
    assert "&amp;" in xml and "&lt;balise&gt;" in xml
    import xml.etree.ElementTree as ET

    ET.fromstring(xml)  # ne lève pas


def test_build_rss_empty_is_still_valid() -> None:
    import xml.etree.ElementTree as ET

    xml = build_rss(
        [], base_url="https://x.eu", title="T", description="D", self_path="/carnet/rss.xml"
    )
    root = ET.fromstring(xml)
    assert root.find("channel").findall("item") == []
