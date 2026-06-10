"""Internationalization (i18n) — fr / en / es / pt-br / vi.

Minimal v3 implementation:
    from app.i18n import t, get_lang_from_request
    label = t("nav_dashboard", lang="en")

The dispatcher uses dict-based catalogs (no .po/.mo) — light, fast, and
sufficient for the UI labels of an ERP. Catalogs live next to this file
as `<lang>.py` modules with a `CATALOG: dict[str, str]`.

Selection order : ?lang= query → user.language (when authenticated) →
Accept-Language header → default "fr".
"""

from __future__ import annotations

import contextlib
from typing import Any

from app.i18n import en as _en
from app.i18n import fr as _fr

SUPPORTED = ("fr", "en", "es", "pt-br", "vi")
DEFAULT = "fr"

_CATALOGS: dict[str, dict[str, str]] = {
    "fr": _fr.CATALOG,
    "en": _en.CATALOG,
}


def _load(lang: str) -> dict[str, str]:
    if lang in _CATALOGS:
        return _CATALOGS[lang]
    try:
        mod = __import__(f"app.i18n.{lang.replace('-', '_')}", fromlist=["CATALOG"])
        _CATALOGS[lang] = getattr(mod, "CATALOG", {})
        return _CATALOGS[lang]
    except ImportError:
        return {}


def t(key: str, lang: str = DEFAULT, **fmt: Any) -> str:
    """Translate ``key`` for the requested language. Falls back to FR then key."""
    lang = (lang or DEFAULT).lower()
    msg = _load(lang).get(key) or _load(DEFAULT).get(key) or key
    if fmt:
        with contextlib.suppress(KeyError, IndexError):
            msg = msg.format(**fmt)
    return msg


def get_lang_from_request(request, user=None) -> str:
    """Detect language from query → user → Accept-Language → default."""
    try:
        query_lang = request.query_params.get("lang")
        if query_lang and query_lang.lower() in SUPPORTED:
            return query_lang.lower()
    except Exception:
        pass
    if user is not None:
        user_lang = getattr(user, "language", None)
        if user_lang and user_lang.lower() in SUPPORTED:
            return user_lang.lower()
    try:
        accept = (request.headers.get("accept-language") or "").lower()
        for tag in accept.split(","):
            tag = tag.split(";", 1)[0].strip()
            for s in SUPPORTED:
                if tag == s or tag.startswith(s.split("-", 1)[0] + "-"):
                    return s
    except Exception:
        pass
    return DEFAULT


__all__ = ["DEFAULT", "SUPPORTED", "get_lang_from_request", "t"]
