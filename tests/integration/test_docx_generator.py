"""Backlog « DOCX generators » — offre commerciale + Bill of Lading (.docx).

Teste les générateurs purs de ``services.docx_generator`` (aucune DB : ils
lisent des attributs simples) en relisant le .docx produit avec python-docx.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

docx = pytest.importorskip("docx")  # python-docx requis (présent en CI)


def _read_text(blob: bytes) -> str:
    """Concatène le texte des paragraphes + cellules de tableau du .docx."""
    document = docx.Document(io.BytesIO(blob))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _leg():
    return SimpleNamespace(
        leg_code="1CFRBR6",
        etd=datetime(2026, 7, 1, 8, 0, tzinfo=UTC),
        eta=datetime(2026, 7, 20, 18, 0, tzinfo=UTC),
    )


def test_build_offer_docx_roundtrip():
    from app.services.docx_generator import DOCX_MIME, build_offer_docx

    offer = SimpleNamespace(
        reference="OFF-2026-001",
        title="Transport vélique palettes Le Havre → Fort-de-France",
        estimated_palettes=12,
        proposed_rate_eur=Decimal("450.00"),
        total_eur=Decimal("5400.00"),
        valid_until=datetime(2026, 8, 1).date(),
        notes="Tarif préférentiel partenaire.",
    )
    client = SimpleNamespace(
        name="Acme Rhum", company_name="Acme SAS", email="ops@acme.fr", phone="+33 1 23 45 67 89"
    )

    doc = build_offer_docx(offer=offer, client=client, leg=_leg())

    assert doc.mime == DOCX_MIME
    assert doc.filename == "Offre_OFF-2026-001.docx"
    assert doc.docx[:2] == b"PK"  # conteneur ZIP (docx)
    text = _read_text(doc.docx)
    assert "OFFRE COMMERCIALE NEWTOWT" in text
    assert "OFF-2026-001" in text
    assert "1CFRBR6" in text
    assert "5 400.00 EUR" in text  # séparateur d'espace
    assert "Tarif préférentiel partenaire." in text


def test_build_offer_docx_without_leg_or_notes():
    from app.services.docx_generator import build_offer_docx

    offer = SimpleNamespace(
        reference="OFF-2",
        title="Devis",
        estimated_palettes=None,
        proposed_rate_eur=None,
        total_eur=None,
        valid_until=None,
        notes=None,
    )
    client = SimpleNamespace(name="X", company_name=None, email="x@x.fr", phone=None)
    doc = build_offer_docx(offer=offer, client=client, leg=None)
    text = _read_text(doc.docx)
    assert "À confirmer" in text
    assert "Notes" not in text  # section omise sans notes


def test_build_bill_of_lading_docx_roundtrip():
    from app.services.docx_generator import build_bill_of_lading_docx

    booking = SimpleNamespace(
        reference="BK-9",
        leg_id=3,
        id=9,
        total_weight_kg=Decimal("1800.00"),
        total_palettes=4,
        signed_terms_version="v2026.1",
        signed_terms_at=datetime(2026, 6, 1, tzinfo=UTC),
        pickup_address="Quai 5, Le Havre",
        delivery_address="Zone portuaire, Fort-de-France",
        items=[
            SimpleNamespace(
                pallet_format="EPAL",
                pallet_count=4,
                cargo_description="Rhum agricole AOC",
                unit_weight_kg=Decimal("450"),
                total_weight_kg=Decimal("1800"),
                hazardous=True,
                imdg_class="3",
                un_number="3065",
            )
        ],
    )
    leg = _leg()
    vessel = SimpleNamespace(name="Anemos", code="ANEM", imo_number="9999999", flag="FR")
    pol = SimpleNamespace(name="Le Havre", locode="FRLEH", country="FR")
    pod = SimpleNamespace(name="Fort-de-France", locode="MQFDF", country="MQ")
    client = SimpleNamespace(
        company_name="Acme SAS",
        contact_name="Jean Acme",
        email="ops@acme.fr",
        billing_address="1 rue X",
        country="FR",
        language="fr",
    )

    doc = build_bill_of_lading_docx(
        booking=booking,
        leg=leg,
        vessel=vessel,
        pol=pol,
        pod=pod,
        client=client,
        bl_number="TUAW_3_9",
    )

    assert doc.filename == "TUAW_3_9.docx"
    assert doc.docx[:2] == b"PK"
    text = _read_text(doc.docx)
    assert "TUAW_3_9" in text
    assert "Anemos" in text and "IMO 9999999" in text
    assert "1CFRBR6" in text
    assert "Rhum agricole AOC" in text
    assert "UN 3065" in text  # marchandise dangereuse
    assert "4 palettes" in text
    assert "La Haye-Visby" in text
    assert "Fort-de-France" in text


def test_docx_routes_registered():
    from app.routers import cargo_router, commercial_router

    cargo_paths = {r.path for r in cargo_router.router.routes}
    assert "/cargo/booking/{ref}/bl.docx" in cargo_paths
    assert "/me/bookings/{ref}/bl.docx" in cargo_paths

    com_paths = {r.path for r in commercial_router.router.routes}
    assert "/offers/{offer_id}/export.docx" in com_paths
