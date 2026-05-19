"""Jinja2 setup + global filters/context."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.config import settings

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _format_money(value: Any, currency: str = "EUR") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f} {currency}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def _format_date(value: Any, fmt: str = "%Y-%m-%d") -> str:
    if value is None:
        return "—"
    try:
        return value.strftime(fmt)
    except AttributeError:
        return str(value)


def _format_datetime(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return "—"
    try:
        return value.strftime(fmt)
    except AttributeError:
        return str(value)


def _flag_emoji(country_code: str | None) -> str:
    if not country_code or len(country_code) != 2:
        return ""
    base = ord("🇦") - ord("A")
    try:
        return chr(ord(country_code[0].upper()) + base) + chr(
            ord(country_code[1].upper()) + base
        )
    except (TypeError, ValueError):
        return ""


# ─────────── i18n helpers ──────────────
from app.i18n import (
    DEFAULT as _i18n_default,
    SUPPORTED as _i18n_supported,
    get_lang_from_request as _i18n_get_lang,
    t as _i18n_t,
)


def _i18n_context_processor(request: Request) -> dict[str, Any]:
    """Inject `lang`, `brand` (lang-aware) et `lang_options` dans chaque template.

    Ordre de priorité pour `lang` :
      1. cookie `towt_lang` (posé via GET /lang/{lang})
      2. query ?lang=
      3. user.language (si staff loggué)
      4. Accept-Language
      5. DEFAULT

    Expose aussi ``notif_count`` lu sur ``request.state`` (pré-chargé par
    ``require_permission()``) → le badge cloche du topbar fonctionne sur
    toutes les pages staff sans répéter la query dans chaque vue.
    """
    cookie_lang = request.cookies.get("towt_lang")
    if cookie_lang and cookie_lang.lower() in _i18n_supported:
        lang = cookie_lang.lower()
    else:
        lang = _i18n_get_lang(request, user=None)
    return {
        "lang": lang,
        "lang_options": list(_i18n_supported),
        "brand": _BRAND_BY_LANG.get(lang, _BRAND_BY_LANG[_i18n_default]),
        "notif_count": getattr(request.state, "notif_count", 0),
    }


# ──────────────────────── Corporate identity (lang-aware) ────────────────
# Source de vérité : Versions TOWT/newtowt-design-tokens.json
_BRAND_LOGOS = {
    "logo_light": "/static/img/logo_NEWTOWT_web.png",
    "logo_dark":  "/static/img/logo_NEWTOWT_web_dark.png",
    "logo_white": "/static/img/logo_NEWTOWT_web_white.png",
    "logo_email": "/static/img/logo_NEWTOWT_email.png",
}

_BRAND_BY_LANG: dict[str, dict[str, Any]] = {
    "fr": {
        "raison_sociale": "TransOceanic Wind Transport — NEWTOWT",
        "nom_court": "NEWTOWT",
        "mention": "Pionnier du transport maritime décarboné depuis 2011",
        "adresse": "52 Quai Frissard - 76600 Le Havre",
        "telephone": "+33 9 84 33 89 62",
        "email": "communication@towt.eu",
        "site_public": "https://towt.eu",
        "tagline_1": "On garde le cap.",
        "tagline_2": "Une nouvelle traversée commence.",
        "year_founded": 2011,
        **_BRAND_LOGOS,
    },
    "en": {
        "raison_sociale": "TransOceanic Wind Transport — NEWTOWT",
        "nom_court": "NEWTOWT",
        "mention": "Pioneer of decarbonised maritime transport since 2011",
        "adresse": "52 Quai Frissard - 76600 Le Havre, France",
        "telephone": "+33 9 84 33 89 62",
        "email": "communication@towt.eu",
        "site_public": "https://towt.eu",
        "tagline_1": "On course.",
        "tagline_2": "A new crossing begins.",
        "year_founded": 2011,
        **_BRAND_LOGOS,
    },
    "es": {
        "raison_sociale": "TransOceanic Wind Transport — NEWTOWT",
        "nom_court": "NEWTOWT",
        "mention": "Pionero del transporte marítimo descarbonizado desde 2011",
        "adresse": "52 Quai Frissard - 76600 Le Havre, Francia",
        "telephone": "+33 9 84 33 89 62",
        "email": "communication@towt.eu",
        "site_public": "https://towt.eu",
        "tagline_1": "Mantenemos el rumbo.",
        "tagline_2": "Una nueva travesía comienza.",
        "year_founded": 2011,
        **_BRAND_LOGOS,
    },
    "pt-br": {
        "raison_sociale": "TransOceanic Wind Transport — NEWTOWT",
        "nom_court": "NEWTOWT",
        "mention": "Pioneiro do transporte marítimo descarbonizado desde 2011",
        "adresse": "52 Quai Frissard - 76600 Le Havre, França",
        "telephone": "+33 9 84 33 89 62",
        "email": "communication@towt.eu",
        "site_public": "https://towt.eu",
        "tagline_1": "Mantemos o rumo.",
        "tagline_2": "Uma nova travessia começa.",
        "year_founded": 2011,
        **_BRAND_LOGOS,
    },
    "vi": {
        "raison_sociale": "TransOceanic Wind Transport — NEWTOWT",
        "nom_court": "NEWTOWT",
        "mention": "Tiên phong vận tải hàng hải giảm cacbon từ 2011",
        "adresse": "52 Quai Frissard - 76600 Le Havre, Pháp",
        "telephone": "+33 9 84 33 89 62",
        "email": "communication@towt.eu",
        "site_public": "https://towt.eu",
        "tagline_1": "Giữ vững hành trình.",
        "tagline_2": "Một chuyến hải trình mới bắt đầu.",
        "year_founded": 2011,
        **_BRAND_LOGOS,
    },
}


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    context_processors=[_i18n_context_processor],
)

templates.env.filters["money"] = _format_money
templates.env.filters["date"] = _format_date
templates.env.filters["datetime"] = _format_datetime
templates.env.filters["flag"] = _flag_emoji


def _t(key: str, lang: str = _i18n_default, **fmt) -> str:
    return _i18n_t(key, lang, **fmt)


templates.env.globals["t"] = _t
templates.env.globals["i18n_default"] = _i18n_default

templates.env.globals["app_name"] = settings.app_name
templates.env.globals["app_version"] = settings.app_version
templates.env.globals["app_env"] = settings.app_env
templates.env.globals["site_url"] = settings.site_url
