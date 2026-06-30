"""Tests du module de récits d'origine (kit B2B2C café).

Vérifie le rendu des gabarits et les **garde-fous** : aucun pourcentage,
« certifié Anemos » (jamais « label »), injection ERP des placeholders.
"""

from __future__ import annotations

import pytest

from app.services import coffee_stories as cs

ALL_LANGS = ("fr", "en", "pt-br")
FORMATS = ("long", "short")


# ───────────────────────── rendu de base ─────────────────────────
@pytest.mark.parametrize("origin", cs.ORIGINS)
@pytest.mark.parametrize("lang", ALL_LANGS)
@pytest.mark.parametrize("fmt", FORMATS)
def test_every_combination_renders(origin: str, lang: str, fmt: str) -> None:
    txt = cs.render_story(origin, lang, fmt, co2_kg=250)
    assert txt and "{" not in txt and "}" not in txt  # tous les champs remplis


def test_unknown_origin_raises() -> None:
    with pytest.raises(KeyError):
        cs.render_story("brazil", "fr", "long")


def test_spanish_falls_back_to_french() -> None:
    assert cs.render_story("colombie", "es", "long", co2_kg=250) == cs.render_story(
        "colombie", "fr", "long", co2_kg=250
    )


# ───────────────────────── garde-fous ─────────────────────────
@pytest.mark.parametrize("origin", cs.ORIGINS)
@pytest.mark.parametrize("lang", ALL_LANGS)
@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("co2", [250, None])
def test_no_percentage_anywhere(origin, lang, fmt, co2) -> None:
    assert "%" not in cs.render_story(origin, lang, fmt, co2_kg=co2)


@pytest.mark.parametrize("origin", cs.ORIGINS)
@pytest.mark.parametrize("lang", ALL_LANGS)
@pytest.mark.parametrize("fmt", FORMATS)
def test_never_says_label(origin, lang, fmt) -> None:
    assert "label" not in cs.render_story(origin, lang, fmt, co2_kg=250).lower()


@pytest.mark.parametrize("origin", cs.ORIGINS)
def test_certified_anemos_mentioned(origin: str) -> None:
    assert "certifié Anemos" in cs.render_story(origin, "fr", "long", co2_kg=250)
    assert "certified by Anemos" in cs.render_story(origin, "en", "long", co2_kg=250)
    assert "certificado pela Anemos" in cs.render_story(origin, "pt-br", "long", co2_kg=250)


# ───────────────────────── injection ERP ─────────────────────────
def test_erp_fields_are_injected() -> None:
    txt = cs.render_story(
        "colombie",
        "fr",
        "long",
        region="Nariño",
        producer="la coopérative El Cóndor",
        vessel="Artemis",
        co2_kg=1200,
    )
    assert "Nariño, Colombie" in txt
    assert "la coopérative El Cóndor" in txt
    assert "l'Artemis" in txt
    assert "1 200 kg de CO₂" in txt  # séparateur de milliers fr (espace insécable)


def test_short_carries_the_kg_number() -> None:
    assert "250 kg of CO₂ avoided" in cs.render_story("mexique", "en", "short", co2_kg=250)


def test_generic_render_has_no_number() -> None:
    txt = cs.render_story("guatemala", "fr", "long")  # aucun co2_kg
    assert "kg" not in txt
    assert "certifié Anemos" in txt


def test_thousands_separator_per_language() -> None:
    assert "1,200 kg of CO₂" in cs.render_story("colombie", "en", "long", co2_kg=1200)


# ───────────────────────── exemples vitrine ─────────────────────────
# ───────────────────────── origines (kit ERP / Vague 3) ─────────────────────────
@pytest.mark.parametrize("origin", cs.ORIGINS)
def test_is_valid_origin_accepts_known(origin: str) -> None:
    assert cs.is_valid_origin(origin)


@pytest.mark.parametrize("bad", ["brazil", "", None, "Colombie", "café"])
def test_is_valid_origin_rejects_unknown(bad) -> None:
    assert not cs.is_valid_origin(bad)


def test_origin_label_per_language() -> None:
    assert cs.origin_label("colombie", "fr") == "Colombie"
    assert cs.origin_label("colombie", "en") == "Colombia"
    assert cs.origin_label("mexique", "pt-br") == "México"
    assert cs.origin_label("mexique", "es") == "Mexique"  # es → fr
    assert cs.origin_label("unknown", "fr") == ""


@pytest.mark.parametrize("origin", cs.ORIGINS)
@pytest.mark.parametrize("lang", ALL_LANGS)
def test_marketing_example_shape(origin: str, lang: str) -> None:
    ex = cs.marketing_example(origin, lang)
    assert set(ex) == {"origin", "title", "region", "producer", "vessel", "co2_kg"}
    assert ex["title"] and ex["region"] and ex["producer"]
    # L'exemple doit produire un récit cohérent et vérifiable.
    txt = cs.render_story(
        origin,
        lang,
        "long",
        region=ex["region"],
        producer=ex["producer"],
        vessel=ex["vessel"],
        co2_kg=ex["co2_kg"],
    )
    assert "%" not in txt and "{" not in txt
