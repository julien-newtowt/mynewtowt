"""CARGO-14 — auto-fill des dimensions par format de palette + alerte IMDG.

`apply_default_dimensions` pré-remplit longueur/largeur depuis le format de
palette sans écraser une saisie ; les formats sans empreinte standard
(barriques) restent inchangés. Le détail PL affiche une alerte si des batches
sont classés dangereux (IMDG).
"""

from __future__ import annotations


def test_apply_default_dimensions_fills_when_blank():
    from app.services.packing_list import apply_default_dimensions

    assert apply_default_dimensions({"pallet_format": "EPAL"}) == {
        "pallet_format": "EPAL",
        "length_cm": 120.0,
        "width_cm": 80.0,
    }


def test_apply_default_dimensions_keeps_user_values():
    from app.services.packing_list import apply_default_dimensions

    out = apply_default_dimensions({"pallet_format": "EPAL", "length_cm": 99.0})
    assert out["length_cm"] == 99.0  # saisie conservée
    assert out["width_cm"] == 80.0  # largeur complétée


def test_apply_default_dimensions_unknown_format_untouched():
    from app.services.packing_list import apply_default_dimensions

    assert apply_default_dimensions({"pallet_format": "BARRIQUE120"}) == {
        "pallet_format": "BARRIQUE120"
    }


def test_imdg_alert_in_detail_template():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/cargo/packing_list_detail.html")[0]
    assert "Marchandises dangereuses (IMDG)" in src
    assert "selectattr('hazardous')" in src
