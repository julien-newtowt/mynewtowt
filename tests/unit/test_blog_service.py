"""Unit tests — service carnet/blog (slugify pur)."""

from __future__ import annotations

import pytest

from app.services.blog import CATEGORIES, slugify


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
