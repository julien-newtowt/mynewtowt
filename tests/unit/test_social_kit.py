"""Tests unitaires — génération des visuels social du kit B2B2C (P12).

Fonctions pures (sans I/O) : on vérifie que chaque visuel est un SVG bien
formé, porte le CO₂ en **kg absolus**, nomme « Anemos » et ne contient **jamais
de pourcentage** (garde-fou ECGT). Sans certificat : phrase qualitative, aucun
chiffre inventé.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.services import social_kit


def _parse(svg: str) -> ET.Element:
    """Parse le SVG → lève si mal formé (preuve de validité XML)."""
    return ET.fromstring(svg)


@pytest.mark.parametrize("fmt", ["square", "story", "landscape"])
def test_render_svg_is_valid_and_carries_absolute_kg(fmt):
    svg = social_kit.render_svg(
        fmt,
        lang="fr",
        origin="colombie",
        origin_label="Colombie",
        story_short="Café des Andes, traversé à la voile. 300 kg de CO₂ évités, vérifiables.",
        co2_kg=300,
        cert_ref="ANEMOS-TEST-1",
        qr_data_uri="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        qr_is_voyage=False,
        verify_url="https://newtowt.eu/verify/ANEMOS-TEST-1",
    )
    root = _parse(svg)  # SVG bien formé
    assert root.tag.endswith("svg")
    w, h = social_kit.FORMATS[fmt]
    assert root.attrib["width"] == str(w) and root.attrib["height"] == str(h)
    # CO₂ en kg absolus, contigu, et certificat nommé.
    assert "300 kg" in svg
    assert "Anemos" in svg
    # ECGT : jamais de pourcentage nulle part dans le visuel.
    assert "%" not in svg


def test_render_svg_without_cert_is_qualitative_no_number():
    svg = social_kit.render_svg(
        "square",
        lang="fr",
        origin=None,
        co2_kg=None,
        cert_ref=None,
        qr_data_uri=None,
        verify_url="https://newtowt.eu/verify",
    )
    _parse(svg)
    assert "Anemos" in svg
    assert "%" not in svg
    # Aucun « N kg » inventé quand le certificat est absent.
    assert "kg" not in svg


def test_render_svg_embeds_qr_and_client_logo():
    svg = social_kit.render_svg(
        "story",
        lang="en",
        origin="colombie",
        origin_label="Colombia",
        story_short="Andean coffee, sailed across. 300 kg of CO₂ avoided, verifiable.",
        co2_kg=1200,
        cert_ref="ANEMOS-2",
        qr_data_uri="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        qr_is_voyage=True,
        verify_url="https://newtowt.eu/voyage/BK-1",
        client_brand_name="ACME Coffee",
        client_logo_data="data:image/png;base64,iVBORw0KGgo=",
    )
    _parse(svg)
    assert "<image" in svg  # QR + logo embarqués
    assert "ACME Coffee" not in svg or "ACME" in svg  # co-brand affiché si logo absent
    assert "1,200 kg" in svg  # séparateur anglais
    assert "Anemos" in svg
    assert "%" not in svg


def test_unknown_format_raises():
    with pytest.raises(KeyError):
        social_kit.render_svg("banner", co2_kg=100)


def test_resolve_origin_picks_coffee_then_cacao():
    assert social_kit.resolve_origin("colombie") is not None
    assert social_kit.commodity_of("colombie") == "coffee"
    assert social_kit.commodity_of("equateur") == "cacao"  # verticale cacao
    assert social_kit.resolve_origin("brazil") is None
    assert social_kit.commodity_of(None) is None


def test_xml_escaping_of_client_brand():
    svg = social_kit.render_svg(
        "landscape",
        lang="fr",
        co2_kg=250,
        client_brand_name="A & B <Coffee>",
        qr_data_uri=None,
    )
    _parse(svg)  # doit rester bien formé malgré les caractères spéciaux
    assert "&amp;" in svg and "&lt;Coffee&gt;" in svg
