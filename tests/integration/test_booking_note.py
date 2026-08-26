"""Booking note contractuelle établie à la validation d'une offre (lot 5).

Vérifie que le document est établi automatiquement, prérempli depuis les données
réelles, corrigeable avant diffusion puis gelé, et que le Word produit contient
bien les conditions générales du transporteur — c'est ce qui en fait un contrat
et non une fiche récapitulative.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.booking_note import BookingNote
from app.models.commercial import (
    Client,
    RateGrid,
    RateGridOption,
    RateGridPaymentTerm,
    RateOffer,
)
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.booking_note import BookingNoteError, ensure_for_offer, mark_issued
from app.services.offer_lifecycle import validate_offer

docx = pytest.importorskip("docx")


def _read_text(blob: bytes) -> str:
    document = docx.Document(io.BytesIO(blob))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


async def _scene(db, *, with_grid=True) -> tuple[RateOffer, Client]:
    db.add_all(
        [
            Port(id=1, locode="FRLEH", name="Le Havre", country="FR"),
            Port(id=2, locode="BRSSZ", name="Santos", country="BR"),
            Vessel(id=1, code="ANE", name="Anemos", capacity_palettes=500),
        ]
    )
    client = Client(
        name="Cacao Négoce SAS",
        client_type="freight_forwarder",
        contact_name="Marie Dupont",
        contact_email="ops@cacao-negoce.fr",
        address="12 quai de la Marne\n76600 Le Havre",
    )
    db.add(client)
    await db.flush()

    leg = Leg(
        leg_code="1AFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=datetime(2026, 9, 1, tzinfo=UTC),
        eta_ref=datetime(2026, 9, 25, tzinfo=UTC),
        etd=datetime(2026, 9, 1, tzinfo=UTC),
        eta=datetime(2026, 9, 25, tzinfo=UTC),
    )
    db.add(leg)
    await db.flush()

    grid_id = None
    if with_grid:
        grid = RateGrid(
            reference="RG-2026-0001",
            client_id=client.id,
            status="active",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            bl_fee=Decimal("75.00"),
        )
        db.add(grid)
        await db.flush()
        db.add(
            RateGridOption(
                grid_id=grid.id,
                code="MANUT",
                label="Manutention portuaire",
                unit="per_palette",
                amount_eur=Decimal("18.00"),
                is_active=True,
            )
        )
        db.add_all(
            [
                RateGridPaymentTerm(
                    grid_id=grid.id, position=1, trigger="days_before_etd",
                    offset_days=30, percentage=Decimal("40.00"),
                ),
                RateGridPaymentTerm(
                    grid_id=grid.id, position=2, trigger="before_loading",
                    percentage=Decimal("60.00"), label="Solde",
                ),
            ]
        )
        await db.flush()
        grid_id = grid.id

    offer = RateOffer(
        reference="RO-2026-0001",
        client_id=client.id,
        grid_id=grid_id,
        leg_id=leg.id,
        title="Transat cacao",
        status="en_cours",
        estimated_palettes=150,
        proposed_rate_eur=Decimal("290.00"),
        total_eur=Decimal("43500.00"),
        valid_until=date(2026, 8, 31),
    )
    db.add(offer)
    await db.flush()
    return offer, client


@pytest.mark.asyncio
async def test_validation_establishes_the_booking_note(db):
    """La règle métier : valider une offre établit la booking note."""
    offer, _client = await _scene(db)
    await validate_offer(db, offer, actor_name="Yasmin")

    note = (
        await db.execute(BookingNote.__table__.select().where(BookingNote.offer_id == offer.id))
    ).fetchone()
    assert note is not None
    assert note.reference.startswith("BN-")
    assert note.status == "brouillon"


@pytest.mark.asyncio
async def test_fields_are_prefilled_from_real_data(db):
    offer, _client = await _scene(db)
    await validate_offer(db, offer)
    note = await ensure_for_offer(db, offer)

    assert note.vessel_name == "Anemos"
    assert note.pol_text == "FRLEH – Le Havre, FR"
    assert note.pod_text == "BRSSZ – Santos, BR"
    assert note.merchant_name == "Cacao Négoce SAS"
    assert note.merchant_contact == "Marie Dupont"
    assert note.merchant_email == "ops@cacao-negoce.fr"
    # « Time for shipment » = ETD du voyage (arbitrage du 2026-08-26).
    assert note.time_for_shipment == "01/09/2026"
    # Conditions tarifaires : tarif, total, grille, frais et options.
    assert "290.00 EUR par palette" in note.freight_terms
    assert "43500.00 EUR" in note.freight_terms
    assert "RG-2026-0001" in note.freight_terms
    assert "Manutention portuaire" in note.freight_terms
    # Échéancier repris de la grille.
    assert "40.00 % — 30 jours avant le départ du navire" in note.payment_terms
    assert "60.00 % — Avant le chargement (Solde)" in note.payment_terms


@pytest.mark.asyncio
async def test_missing_data_stays_empty_rather_than_invented(db):
    """Un contrat qui affirme une information fausse est pire qu'un contrat à compléter."""
    offer, _client = await _scene(db, with_grid=False)
    await validate_offer(db, offer)
    note = await ensure_for_offer(db, offer)

    # L'agent au POD n'est pas une donnée du système.
    assert note.agents_pod is None
    # Sans grille, pas d'échéancier inventé.
    assert note.payment_terms == ""


@pytest.mark.asyncio
async def test_establishment_is_idempotent(db):
    """Revalider ne fabrique pas un second contrat, et ne perd pas les corrections."""
    offer, _client = await _scene(db)
    await validate_offer(db, offer)
    note = await ensure_for_offer(db, offer)
    note.agents_pod = "Agence Santos Ltda"
    await db.flush()

    again = await ensure_for_offer(db, offer)
    assert again.id == note.id
    assert again.agents_pod == "Agence Santos Ltda"  # correction préservée


@pytest.mark.asyncio
async def test_booking_note_is_refused_on_an_unvalidated_offer(db):
    offer, _client = await _scene(db)
    with pytest.raises(BookingNoteError):
        await ensure_for_offer(db, offer)


@pytest.mark.asyncio
async def test_docx_contains_the_prefilled_fields_and_the_contract_terms(db):
    from app.services.docx_generator import build_booking_note_docx

    offer, _client = await _scene(db)
    await validate_offer(db, offer)
    note = await ensure_for_offer(db, offer)

    doc = build_booking_note_docx(note=note, offer=offer)
    text = _read_text(doc.docx)

    assert note.reference in text
    assert "Anemos" in text
    assert "FRLEH – Le Havre, FR" in text
    assert "Cacao Négoce SAS" in text
    assert "NEWTOWT" in text and "52 Quai Frissard" in text
    # Les conditions générales font le contrat : sans elles, ce n'est qu'un récapitulatif.
    assert "Hague-Visby" in text
    assert "ANEMOS" in text
    assert "U.S. COGSA" in text
    assert "Signature (Merchant)" in text and "Signature (Carrier)" in text
    # Un brouillon doit le dire — il circule parfois avant diffusion.
    assert "BROUILLON" in text


@pytest.mark.asyncio
async def test_issued_document_drops_the_draft_marker_and_is_frozen(db):
    from app.services.docx_generator import build_booking_note_docx

    offer, _client = await _scene(db)
    await validate_offer(db, offer)
    note = await ensure_for_offer(db, offer)

    await mark_issued(db, note, user_name="Yasmin")
    assert note.status == "diffusee"
    assert note.is_editable() is False

    text = _read_text(build_booking_note_docx(note=note, offer=offer).docx)
    assert "BROUILLON" not in text

    with pytest.raises(BookingNoteError):
        await mark_issued(db, note)


@pytest.mark.asyncio
async def test_free_text_is_written_as_text_not_as_markup(db):
    """Le document est construit par python-docx : aucun texte saisi n'est interprété."""
    from app.services.docx_generator import build_booking_note_docx

    offer, _client = await _scene(db)
    await validate_offer(db, offer)
    note = await ensure_for_offer(db, offer)
    note.special_terms = 'Remise <w:t>&amp; conditions</w:t> "spéciales"'
    await db.flush()

    text = _read_text(build_booking_note_docx(note=note, offer=offer).docx)
    assert 'Remise <w:t>&amp; conditions</w:t> "spéciales"' in text
