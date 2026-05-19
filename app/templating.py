"""Jinja2 setup + global filters/context."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates

from app.config import settings

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
    # Build regional indicator symbols
    base = ord("🇦") - ord("A")
    try:
        return chr(ord(country_code[0].upper()) + base) + chr(
            ord(country_code[1].upper()) + base
        )
    except (TypeError, ValueError):
        return ""


templates.env.filters["money"] = _format_money
templates.env.filters["date"] = _format_date
templates.env.filters["datetime"] = _format_datetime
templates.env.filters["flag"] = _flag_emoji

templates.env.globals["app_name"] = settings.app_name
templates.env.globals["app_version"] = settings.app_version
templates.env.globals["app_env"] = settings.app_env
templates.env.globals["site_url"] = settings.site_url

# ──────────────────────── Corporate identity ─────────────────────────────
# Source de vérité : Versions TOWT/newtowt-design-tokens.json
templates.env.globals["brand"] = {
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
    # Logos
    "logo_light": "/static/img/logo_NEWTOWT_web.png",
    "logo_dark": "/static/img/logo_NEWTOWT_web_dark.png",
    "logo_white": "/static/img/logo_NEWTOWT_web_white.png",
    "logo_email": "/static/img/logo_NEWTOWT_email.png",
}
