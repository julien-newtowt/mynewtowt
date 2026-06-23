"""STO-06 — bilinguisme FR/EN du plan d'arrimage (labels zones + PDF)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.stowage import stowage_pdf_labels, zone_label


def test_zone_label_bilingual():
    # FR (défaut) — indice cale/pont/bloc.
    fr = zone_label("INF_AR_MIL")
    assert fr == "INF_AR_MIL — cale AR, pont INF, bloc MIL"
    # EN — vocabulaire maritime anglais.
    en = zone_label("INF_AR_MIL", "en")
    assert en == "INF_AR_MIL — aft hold, lower deck, mid block"
    assert zone_label("SUP_AV_AV", "en") == "SUP_AV_AV — fwd hold, upper deck, fwd block"


def test_zone_label_unknown_lang_falls_back_to_fr():
    assert zone_label("INF_AR_AR", "vi") == zone_label("INF_AR_AR", "fr")


def test_zone_label_tolerant():
    assert zone_label("", "en") == ""
    assert zone_label("OVERFLOW", "en") == "OVERFLOW"  # non conforme → brut


def test_pdf_labels_fr_en():
    fr = stowage_pdf_labels("fr")
    en = stowage_pdf_labels("en")
    assert fr["assigned_lots"] == "Lots affectés"
    assert en["assigned_lots"] == "Assigned lots"
    assert en["doc_kind"] == "Stowage plan"
    assert en["deck"]["SUP"] == "Upper deck"
    # Langue inconnue → FR.
    assert stowage_pdf_labels("vi") == fr
    # Parité des clés FR/EN.
    assert set(fr.keys()) == set(en.keys())


def test_pdf_template_renders_in_english():
    """Le template PDF compile et produit de l'anglais avec labels EN + lang=en."""
    from types import SimpleNamespace

    from app.services.stowage import parse_zone
    from app.templating import brand_for_lang, templates

    evaluation = {
        "vessel_class": "phoenix",
        "zones": {
            "INF_AR_AR": {
                "pct": 50,
                "capacity_epal": 20,
                "pallet_count": 10,
                "warnings": [],
            }
        },
        "warnings": [],
        "totals": {"pallet_count": 10, "used_t": 5.0, "max_load_t": 100.0},
    }
    item = SimpleNamespace(
        zone="INF_AR_AR",
        batch_id=None,
        description="Green coffee",
        pallet_format="EPAL",
        pallet_count=10,
        weight_kg=3000,
        imdg_class=None,
        hs_code=None,
        is_stacked=True,
    )
    tpl = templates.get_template("pdf/stowage_plan.html")
    html = tpl.render(
        leg=SimpleNamespace(leg_code="1CFRBR6"),
        vessel=SimpleNamespace(name="Anemos", imo_number="123"),
        pol=SimpleNamespace(name="Fécamp"),
        pod=SimpleNamespace(name="Santos"),
        plan=SimpleNamespace(status="draft"),
        items=[item],
        evaluation=evaluation,
        decks=("SUP", "MIL", "INF"),
        holds=("AR", "AV"),
        blocks=("AR", "MIL", "AV"),
        zone_label=zone_label,
        parse_zone=parse_zone,
        lang="en",
        labels=stowage_pdf_labels("en"),
        brand=brand_for_lang("en"),
        issued_at=datetime(2026, 4, 1, tzinfo=UTC),
        site_url="https://example.test",
    )
    # Anglais présent…
    assert "Assigned lots" in html
    assert "Loading diagram" in html
    assert "Upper deck" in html
    assert "aft hold, lower deck, aft block" in html  # zone_label EN
    assert "stacked" in html
    # …et le FR équivalent absent.
    assert "Lots affectés" not in html
    assert "Pont sup." not in html
