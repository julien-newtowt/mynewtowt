"""UX — reprise (UX-01 fuseau horaire, UX-04 cloche notif, UX-05 sélecteur langue)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace


def _macro():
    from app.templating import templates

    return templates.env.get_template("staff/_time_input.html").module.tz_datetime


def _render_topbar(**ctx):
    from app.templating import _BRAND_BY_LANG, templates

    base = {
        "request": SimpleNamespace(url=SimpleNamespace(path="/dashboard")),
        "lang": "fr",
        "brand": _BRAND_BY_LANG["fr"],
        "user": SimpleNamespace(full_name="Op", username="op", role="operation"),
        "notif_count": 0,
        "recent_notifications": [],
        "lang_options": ["fr", "en", "es", "pt-br", "vi"],
        "newtowt_agent_enabled": True,
    }
    base.update(ctx)
    return templates.env.get_template("staff/_topbar.html").render(**base)


# ─────────────────────────────── UX-01 ───────────────────────────────


def test_tz_datetime_macro_renders_wrapper_and_select():
    html = str(_macro()("planned_start", label="Début prévu"))
    assert 'class="tz-input-wrap"' in html
    assert 'name="planned_start"' in html
    assert 'type="datetime-local"' in html
    assert 'class="tz-select"' in html
    assert 'name="planned_start_tz"' in html  # champ compagnon de fuseau
    assert "tz-utc-hint" in html  # zone d'aperçu UTC (câblée par towt-tz.js)
    # Les 3 fuseaux V2 : port local / Paris / UTC.
    assert 'value="port_local"' in html
    assert 'value="Europe/Paris"' in html
    assert 'value="UTC"' in html


def test_tz_datetime_macro_formats_datetime_value():
    html = str(_macro()("etd", value=datetime(2026, 4, 1, 8, 30)))
    assert 'value="2026-04-01T08:30"' in html


def test_tz_datetime_macro_accepts_string_value():
    """Une valeur déjà sous forme de chaîne ne doit pas planter (pas de strftime)."""
    html = str(_macro()("etd", value="2026-04-01T08:30"))
    assert 'value="2026-04-01T08:30"' in html


def test_tz_datetime_macro_required_flag():
    html = str(_macro()("occurred_at", required=True))
    assert "required" in html
    # sans required → pas d'attribut
    assert "required" not in str(_macro()("x"))


def test_templates_using_time_input_compile():
    """Les templates qui importent le partial compilent sans erreur."""
    from app.templating import templates

    for name in ("staff/escale/index.html", "staff/captain/index.html"):
        assert templates.env.get_template(name) is not None


# ─────────────────────────────── UX-04 ───────────────────────────────


def test_topbar_notif_menu_lists_recent():
    notifs = [
        SimpleNamespace(
            title="Nouvelle commande",
            detail="ORD-2026-0001",
            link="/commercial/orders/1",
            is_read=False,
        ),
        SimpleNamespace(title="EOSP", detail=None, link=None, is_read=True),
    ]
    html = _render_topbar(recent_notifications=notifs, notif_count=1)
    assert "Nouvelle commande" in html
    assert "/commercial/orders/1" in html
    assert "notif-unread" in html  # la non-lue est mise en avant


def test_topbar_notif_menu_empty_state():
    html = _render_topbar(recent_notifications=[])
    assert "Aucune notification" in html


# ─────────────────────────────── UX-05 ───────────────────────────────


def test_topbar_lang_switcher_present():
    html = _render_topbar()
    # un lien par langue vers /lang/{l}
    assert "/lang/en" in html
    assert "/lang/es" in html
    assert 'data-lang="vi"' in html
    assert "topbar-lang-menu" in html


def test_topbar_lang_switcher_hidden_when_single_lang():
    html = _render_topbar(lang_options=["fr"])
    assert "topbar-lang-menu" not in html
