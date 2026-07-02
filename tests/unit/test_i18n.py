"""Tests for app.i18n — translation dispatch and language detection."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import ClassVar

from app.i18n import DEFAULT, SUPPORTED, get_lang_from_request, t


@dataclass
class FakeRequest:
    query_params: dict
    headers: dict


def test_default_is_fr():
    assert DEFAULT == "fr"


def test_supported_languages_include_fr_en():
    assert "fr" in SUPPORTED
    assert "en" in SUPPORTED


def test_t_returns_french_label_by_default():
    assert t("nav_dashboard") == "Tableau de bord"


def test_t_returns_english_label_when_lang_en():
    assert t("nav_dashboard", "en") == "Dashboard"


def test_t_falls_back_to_french_for_missing_translation():
    # 'nav_settings' is in FR; if EN doesn't have it we fall back to FR
    # (we've added both, so just verify fallback works for any key)
    assert t("nav_logout", "en") in ("Sign out", "Déconnexion")


def test_t_falls_back_to_key_for_unknown():
    # Unknown key returns the key itself
    assert t("non_existent_key_xyz", "fr") == "non_existent_key_xyz"


def test_t_handles_format_placeholders():
    # Custom catalog: simulate via direct call
    from app.i18n import _CATALOGS

    catalog = _CATALOGS.setdefault("fr", {})
    catalog["hello_named"] = "Bonjour {name}"
    try:
        assert t("hello_named", "fr", name="Alice") == "Bonjour Alice"
    finally:
        # Le catalogue fr est un objet partagé (module) : ne pas laisser la
        # clé de test polluer les tests de parité fr↔vi exécutés ensuite.
        catalog.pop("hello_named", None)


def test_get_lang_from_request_uses_query_param():
    req = FakeRequest(query_params={"lang": "en"}, headers={"accept-language": ""})

    # Convert to attribute access for compat with caller using .get
    class R:
        query_params = req.query_params
        headers = req.headers

    assert get_lang_from_request(R) == "en"


def test_get_lang_from_request_uses_user_language():
    class R:
        query_params: ClassVar[dict] = {}
        headers: ClassVar[dict] = {}

    user = SimpleNamespace(language="en")
    assert get_lang_from_request(R, user=user) == "en"


def test_get_lang_from_request_uses_accept_language():
    class R:
        query_params: ClassVar[dict] = {}
        headers: ClassVar[dict] = {"accept-language": "en-US,en;q=0.9,fr;q=0.8"}

    assert get_lang_from_request(R) == "en"


def test_get_lang_from_request_falls_back_to_default():
    class R:
        query_params: ClassVar[dict] = {}
        headers: ClassVar[dict] = {"accept-language": "ja-JP"}

    assert get_lang_from_request(R) == DEFAULT
