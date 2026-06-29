"""ESC-08 (tranche) — liens d'impression du plan d'arrimage FR/EN sur l'escale."""

from __future__ import annotations


def test_escale_has_stowage_print_links():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/escale/index.html")[0]
    assert "/plan.pdf?lang=fr" in src
    assert "/plan.pdf?lang=en" in src
