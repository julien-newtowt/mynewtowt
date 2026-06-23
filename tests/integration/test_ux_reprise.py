"""UX — reprise (UX-01 saisie de fuseau horaire dans les formulaires)."""

from __future__ import annotations

from datetime import datetime


def _macro():
    from app.templating import templates

    return templates.env.get_template("staff/_time_input.html").module.tz_datetime


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
