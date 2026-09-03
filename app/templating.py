"""Jinja2 setup + global filters/context."""

from __future__ import annotations

import json as _json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.config import settings
from app.i18n import (
    DEFAULT as _i18n_default,
)
from app.i18n import (
    SUPPORTED as _i18n_supported,
)
from app.i18n import (
    get_lang_from_request as _i18n_get_lang,
)
from app.i18n import (
    t as _i18n_t,
)
from app.services.seo import organization_jsonld as _org_jsonld

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _format_qty(value: Any) -> str:
    """Quantité/stock ``Decimal`` → notation positionnelle, sans zéros de fin.

    ``Decimal.normalize()`` seul produit de la notation SCIENTIFIQUE pour les
    multiples de 10 — ``Decimal("30.000").normalize() == Decimal("3E+1")``,
    affiché « 3E+1 » tel quel (constaté sur le stock de bord, 2026-09-01).
    ``format(d, "f")`` force la forme positionnelle : 30.000 → « 30 »,
    2.500 → « 2.5 », 0.000 → « 0 ». À utiliser pour TOUTE quantité Decimal
    affichée — jamais ``.normalize()`` nu dans un template.
    """
    if value is None:
        return ""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    return format(d.normalize(), "f")


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
        return chr(ord(country_code[0].upper()) + base) + chr(ord(country_code[1].upper()) + base)
    except (TypeError, ValueError):
        return ""


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
    # PLN-04 — une vue peut imposer la langue du rendu (ex. partage planning
    # public dans la langue choisie à la création) via ``request.state``. Cette
    # surcharge prime sur le cookie / l'Accept-Language du visiteur.
    forced_lang = getattr(request.state, "forced_lang", None)
    cookie_lang = request.cookies.get("towt_lang")
    if forced_lang and str(forced_lang).lower() in _i18n_supported:
        lang = str(forced_lang).lower()
    elif cookie_lang and cookie_lang.lower() in _i18n_supported:
        lang = cookie_lang.lower()
    else:
        lang = _i18n_get_lang(request, user=None)
    return {
        "lang": lang,
        "lang_options": list(_i18n_supported),
        "brand": _BRAND_BY_LANG.get(lang, _BRAND_BY_LANG[_i18n_default]),
        "notif_count": getattr(request.state, "notif_count", 0),
        "recent_notifications": getattr(request.state, "recent_notifications", []),
        "newtowt_agent_enabled": getattr(request.state, "newtowt_agent_enabled", True),
    }


# ──────────────────────── Corporate identity (lang-aware) ────────────────
# Source de vérité : docs/design/newtowt-design-tokens.json
_BRAND_LOGOS = {
    "logo_light": "/static/img/logo_NEWTOWT_web.png",
    "logo_dark": "/static/img/logo_NEWTOWT_web_dark.png",
    "logo_white": "/static/img/logo_NEWTOWT_web_white.png",
    "logo_email": "/static/img/logo_NEWTOWT_email.png",
}

_BRAND_BY_LANG: dict[str, dict[str, Any]] = {
    "fr": {
        "raison_sociale": "TransOceanic Wind Transport — NEWTOWT",
        "nom_court": "NEWTOWT",
        "mention": "Pionnier du transport de marchandises à la voile depuis 2011",
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
        "mention": "Pioneer of sail-powered cargo transport since 2011",
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
        "mention": "Pionero del transporte de mercancías a vela desde 2011",
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
        "mention": "Pioneiro do transporte de mercadorias à vela desde 2011",
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
        "mention": "Tiên phong vận tải hàng hóa bằng buồm từ 2011",
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


def brand_for_lang(lang: str | None) -> dict[str, Any]:
    """Dict d'identité corporate pour une langue (fallback FR).

    Utile aux rendus hors-requête (PDF WeasyPrint via ``get_template().render()``)
    où le context processor n'injecte pas ``brand``.
    """
    return _BRAND_BY_LANG.get((lang or "").lower(), _BRAND_BY_LANG[_i18n_default])


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    context_processors=[_i18n_context_processor],
)

templates.env.filters["money"] = _format_money
templates.env.filters["qty"] = _format_qty
templates.env.filters["date"] = _format_date
templates.env.filters["datetime"] = _format_datetime
templates.env.filters["flag"] = _flag_emoji


def _t(key: str, lang: str = _i18n_default, **fmt) -> str:
    return _i18n_t(key, lang, **fmt)


templates.env.globals["t"] = _t
templates.env.globals["i18n_default"] = _i18n_default

# SEC-03 — helper de visibilité sidebar : masque les entrées d'un module
# auquel le rôle n'a pas accès (évite les liens menant à un 403). Basé sur la
# matrice PAR DÉFAUT (affichage uniquement ; le contrôle d'accès effectif reste
# appliqué sur le chemin requête par require_permission()).
from app.permissions import has_any_access as _has_any_access  # noqa: E402

templates.env.globals["can_access"] = _has_any_access


def _leg_phase_label(phase: str | None) -> str:
    """Libellé d'une phase de leg, ou chaîne vide si la phase est inconnue.

    Import différé : ``services.planning`` tire les modèles, et ``templating``
    est importé très tôt — l'importer au niveau module créerait un cycle.
    """
    from app.services.planning import LEG_PHASE_LABELS

    return LEG_PHASE_LABELS.get(phase or "", "")


# Libellés des phases de leg, servis depuis leur source unique
# (``services.planning.LEG_PHASE_LABELS``). Les recopier dans un gabarit
# expose au piège documenté au CLAUDE.md : une phase non traitée tombe dans
# le ``{% else %}`` et affiche une information fausse.
templates.env.globals["leg_phase_label"] = _leg_phase_label

templates.env.globals["app_name"] = settings.app_name
templates.env.globals["app_version"] = settings.app_version
templates.env.globals["app_env"] = settings.app_env
templates.env.globals["site_url"] = settings.site_url


# ─────────── Cache-busting des assets statiques (JS/CSS) ──────────────────
# Les fichiers /static/* sont mis en cache par le navigateur ; sans empreinte,
# une nouvelle version de JS/CSS n'est pas re-téléchargée après déploiement.
# ``asset('js/foo.js')`` ajoute ``?v=<mtime>`` → le cache est invalidé dès que
# le fichier change (et reste stable sinon). mtime mémorisé (faible coût).
_STATIC_DIR = Path(__file__).parent / "static"
_asset_v_cache: dict[str, int] = {}


def _asset(path: str) -> str:
    rel = path.lstrip("/")
    if rel.startswith("static/"):
        rel = rel[len("static/") :]
    v = _asset_v_cache.get(rel)
    if v is None:
        try:
            v = int((_STATIC_DIR / rel).stat().st_mtime)
        except OSError:
            v = 0
        _asset_v_cache[rel] = v
    return f"/static/{rel}?v={v}"


templates.env.globals["asset"] = _asset

# ─────────── Kit B2B2C — récits d'origine café (gabarits ERP) ──────────────
# Exposés à la vitrine (/solutions/cafe). render_story() renvoie du texte brut
# (auto-échappé par Jinja) ; les valeurs injectées peuvent venir d'une saisie.
from app.services import coffee_stories as _coffee  # noqa: E402

templates.env.globals["coffee_story"] = _coffee.render_story
templates.env.globals["coffee_origins"] = list(_coffee.ORIGINS)
templates.env.globals["coffee_example"] = _coffee.marketing_example

# Verticale sœur (/solutions/cacao) — même contrat, mêmes garde-fous.
from app.services import cacao_stories as _cacao  # noqa: E402

templates.env.globals["cacao_story"] = _cacao.render_story
templates.env.globals["cacao_origins"] = list(_cacao.ORIGINS)
templates.env.globals["cacao_example"] = _cacao.marketing_example

# ─────────── SEO / lisibilité IA (Schema.org + hreflang) ──────────────────
# Fiche Organisation injectée dans le <head> de la vitrine (bloc de données
# `application/ld+json` — non exécuté, compatible CSP stricte).
templates.env.globals["organization_jsonld"] = _json.dumps(
    _org_jsonld(settings.site_url), ensure_ascii=False
)
templates.env.globals["public_langs"] = ["fr", "en", "es", "pt-br"]
templates.env.globals["hreflang_map"] = {
    "fr": "fr",
    "en": "en",
    "es": "es",
    "pt-br": "pt-BR",
}
# Drapeau (code pays ISO-2 pour le filtre |flag) + libellé natif par langue.
templates.env.globals["lang_country"] = {
    "fr": "FR",
    "en": "GB",
    "es": "ES",
    "pt-br": "BR",
}
templates.env.globals["lang_name"] = {
    "fr": "Français",
    "en": "English",
    "es": "Español",
    "pt-br": "Português",
}
