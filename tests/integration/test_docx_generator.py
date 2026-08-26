"""Backlog « DOCX generators » — offre commerciale + Bill of Lading (.docx).

Teste les générateurs purs de ``services.docx_generator`` (aucune DB : ils
lisent des attributs simples) en relisant le .docx produit avec python-docx.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
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


def _batch(*, bl_state="master_signed"):
    """Lot de packing list minimal pour le BL Word (rail registre)."""
    signed = bl_state in ("master_signed", "final")
    return SimpleNamespace(
        id=1,
        batch_number=1,
        pallet_format="EPAL",
        pallet_count=4,
        weight_kg=Decimal("1800"),
        hs_code="220840",
        hazardous=True,
        imdg_class="3",
        un_number="3065",
        description_of_goods="Rhum agricole AOC",
        type_of_goods=None,
        marks_and_numbers="LOT 12/26",
        shipper_name="Acme SAS",
        shipper_address="1 rue X",
        shipper_postal="76600",
        shipper_city="Le Havre",
        shipper_country="FR",
        consignee_name="Distri MQ",
        consignee_address="Zone portuaire",
        consignee_postal=None,
        consignee_city="Fort-de-France",
        consignee_country="MQ",
        notify_name=None,
        notify_address=None,
        notify_postal=None,
        notify_city=None,
        notify_country=None,
        bl_state=bl_state,
        bl_signed_at=datetime(2026, 8, 12, 16, 0, tzinfo=UTC) if signed else None,
        bl_signed_by_name="Cdt Le Bihan" if signed else None,
        bl_signature_hash=("a" * 64) if signed else None,
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


def test_build_bill_of_lading_docx_from_pl_roundtrip():
    """BL Word depuis un lot de packing list — remplace la version « booking ».

    §5.4 — l'ancien générateur partait d'un `Booking` et fabriquait un numéro à la
    volée (`TUAW_{leg_id}_{booking_id}`) jamais enregistré. Il écrivait aussi « Trois
    originaux signés (3 OBL) » **inconditionnellement**, y compris sur un document que
    personne n'avait signé — et c'est le format **éditable**, donc celui qui circule.
    """
    from app.services.docx_generator import build_bill_of_lading_docx_from_pl

    batch = _batch(bl_state="master_signed")
    doc = build_bill_of_lading_docx_from_pl(
        pl=SimpleNamespace(id=1, status="locked"),
        batch=batch,
        leg=_leg(),
        vessel=SimpleNamespace(name="Anemos", code="ANEM", imo_number="9999999", flag="FR"),
        pol=SimpleNamespace(name="Le Havre", locode="FRLEH", country="FR"),
        pod=SimpleNamespace(name="Fort-de-France", locode="MQFDF", country="MQ"),
        bl_number="TUAW_1CFRBR6_001",
        shipped_on_board=SimpleNamespace(value=date(2026, 8, 12)),
    )

    assert doc.filename == "TUAW_1CFRBR6_001.docx"
    assert doc.docx[:2] == b"PK"
    text = _read_text(doc.docx)
    assert "TUAW_1CFRBR6_001" in text
    assert "Anemos" in text and "IMO 9999999" in text
    assert "1CFRBR6" in text
    assert "Rhum agricole AOC" in text
    assert "UN 3065" in text  # marchandise dangereuse
    assert "La Haye-Visby" in text
    assert "Fort-de-France" in text
    # Signé : la mention des originaux, le signataire et l'empreinte sont là.
    assert "Trois originaux signés" in text
    assert "Cdt Le Bihan" in text
    assert "12/08/2026" in text  # shipped on board


def test_the_docx_draft_claims_no_signed_original():
    """🔴 Le défaut de l'ancien générateur, corrigé : ne pas affirmer une signature
    inexistante sur le format le plus facilement transmis."""
    from app.services.docx_generator import build_bill_of_lading_docx_from_pl

    doc = build_bill_of_lading_docx_from_pl(
        pl=SimpleNamespace(id=1, status="draft"),
        batch=_batch(bl_state="draft"),
        leg=_leg(),
        vessel=None,
        pol=None,
        pod=None,
        bl_number="TUAW_1CFRBR6_001",
    )
    text = _read_text(doc.docx)
    assert "Trois originaux signés" not in text
    assert "SANS VALEUR DE TITRE" in text
    assert "Aucun original signé à ce stade" in text
    # Le nom du fichier distingue le brouillon.
    assert doc.filename == "TUAW_1CFRBR6_001-DRAFT.docx"


def test_the_docx_says_the_shipped_on_board_date_is_not_established():
    """Omise plutôt qu'inventée — une date fausse serait une fraude documentaire."""
    from app.services.docx_generator import build_bill_of_lading_docx_from_pl

    doc = build_bill_of_lading_docx_from_pl(
        pl=SimpleNamespace(id=1, status="draft"),
        batch=_batch(bl_state="master_signed"),
        leg=_leg(),
        vessel=None,
        pol=None,
        pod=None,
        bl_number="TUAW_1CFRBR6_001",
        shipped_on_board=None,
    )
    assert "non constatée" in _read_text(doc.docx)


def test_docx_routes_registered():
    from app.routers import cargo_packing_router, cargo_router, commercial_router

    cargo_paths = {r.path for r in cargo_router.router.routes}
    packing_paths = {r.path for r in cargo_packing_router.router.routes}
    # §5.4 — le rail booking est retiré : le BL Word vit sur le rail registre.
    assert "/cargo/booking/{ref}/bl.docx" not in cargo_paths
    assert "/me/bookings/{ref}/bl.docx" not in cargo_paths
    assert "/me/bookings/{ref}/bl/{batch_id}.docx" in cargo_paths
    # `router.routes` expose les chemins **préfixés** (`prefix="/cargo/packing-lists"`).
    assert "/cargo/packing-lists/{pl_id}/batches/{batch_id}/bl.docx" in packing_paths

    # ``router.routes`` expose les chemins **préfixés** : le router commercial
    # est monté avec ``APIRouter(prefix="/commercial")``, l'assertion doit donc
    # porter le préfixe (le test l'omettait et échouait en silence, faute de
    # `tests/integration` en CI).
    com_paths = {r.path for r in commercial_router.router.routes}
    assert "/commercial/offers/{offer_id}/export.docx" in com_paths
