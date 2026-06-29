"""Générateurs de documents Word (.docx) — backlog « DOCX generators ».

Regroupe les générateurs Word de la plateforme, en miroir de
``services.pdf_generator`` (qui produit les PDF via WeasyPrint) :

- ``build_offer_docx``           : offre commerciale (depuis ``RateOffer``).
- ``build_bill_of_lading_docx``  : Bill of Lading / connaissement (depuis un
  ``Booking`` confirmé).

``python-docx`` est importé paresseusement : la dépendance n'est pas toujours
présente en dev et reste lourde à charger.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Teal NEWTOWT (charte « Nouvelle Étoile ») — couleur d'accent des titres.
_TEAL = (0x0D, 0x59, 0x66)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass
class DocxBytes:
    """Document Word sérialisé prêt à servir (téléchargement)."""

    docx: bytes
    filename: str
    mime: str = DOCX_MIME


# ---------------------------------------------------------------------------
# Helpers de mise en forme (chartés)
# ---------------------------------------------------------------------------


def _new_document():
    from docx import Document

    return Document()


def _teal_color():
    from docx.shared import RGBColor

    return RGBColor(*_TEAL)


def _title(doc, text: str) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    heading = doc.add_heading("", level=0)
    run = heading.add_run(text)
    run.font.color.rgb = _teal_color()
    run.font.size = Pt(20)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _section(doc, text: str):
    heading = doc.add_heading(text, level=2)
    if heading.runs:
        heading.runs[0].font.color.rgb = _teal_color()
    return heading


def _kv_table(doc, rows: list[tuple[str, str]]):
    """Table clé/valeur (2 colonnes, libellé en gras)."""
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text = label
        if cells[0].paragraphs[0].runs:
            cells[0].paragraphs[0].runs[0].bold = True
        cells[1].text = value if value is not None else "—"
    return table


def _footer(doc) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    doc.add_paragraph()  # spacer
    para = doc.add_paragraph("NEWTOWT — Pioneer of wind-powered cargo since 2011 — www.newtowt.eu")
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in para.runs:
        run.font.color.rgb = _teal_color()
        run.font.size = Pt(9)
        run.italic = True


def _serialize(doc, filename: str) -> DocxBytes:
    buf = io.BytesIO()
    doc.save(buf)
    return DocxBytes(docx=buf.getvalue(), filename=filename)


def _fmt_eur(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} EUR".replace(",", " ")


def _fmt_date(value, fmt: str = "%d/%m/%Y") -> str:
    return value.strftime(fmt) if value else "—"


# ---------------------------------------------------------------------------
# Offre commerciale
# ---------------------------------------------------------------------------


def build_offer_docx(*, offer, client, leg) -> DocxBytes:
    """Offre commerciale Word depuis un ``RateOffer`` (+ client + leg optionnel)."""
    doc = _new_document()

    _title(doc, "OFFRE COMMERCIALE NEWTOWT")
    _ref_centered(doc, f"Référence : {offer.reference}")
    doc.add_paragraph()  # spacer

    _section(doc, "Client")
    client_rows = [("Nom", client.name if client else "—")]
    if client and getattr(client, "company_name", None):
        client_rows.append(("Société", client.company_name))
    client_rows.append(("E-mail", client.email if client else "—"))
    client_rows.append(("Téléphone", getattr(client, "phone", None) if client else "—"))
    _kv_table(doc, client_rows)
    doc.add_paragraph()

    _section(doc, "Objet")
    doc.add_paragraph(offer.title or "—")
    doc.add_paragraph()

    _section(doc, "Itinéraire")
    if leg:
        doc.add_paragraph(
            f"Leg : {leg.leg_code}\n" f"ETD : {_fmt_date(leg.etd)}     ETA : {_fmt_date(leg.eta)}"
        )
    else:
        doc.add_paragraph("À confirmer")
    doc.add_paragraph()

    _section(doc, "Tarification")
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for idx, label in enumerate(["Description", "Quantité", "Tarif unitaire", "Total"]):
        table.rows[0].cells[idx].text = label
        if table.rows[0].cells[idx].paragraphs[0].runs:
            table.rows[0].cells[idx].paragraphs[0].runs[0].bold = True
    rate = offer.proposed_rate_eur
    rate_str = f"{rate:,.2f} EUR/palette".replace(",", " ") if rate is not None else "—"
    cells = table.add_row().cells
    cells[0].text = "Fret palettes (voilier cargo)"
    cells[1].text = str(offer.estimated_palettes or 0)
    cells[2].text = rate_str
    cells[3].text = _fmt_eur(offer.total_eur)
    doc.add_paragraph()

    _section(doc, "Conditions")
    cond = doc.add_paragraph()
    cond.add_run(f"Validité : {_fmt_date(offer.valid_until)}\n")
    cond.add_run(
        "Ce prix inclut le transport par voilier cargo à propulsion vélique "
        "(zéro émission directe)."
    )

    if offer.notes:
        doc.add_paragraph()
        _section(doc, "Notes")
        doc.add_paragraph(offer.notes)

    _footer(doc)
    return _serialize(doc, f"Offre_{offer.reference}.docx")


def _ref_centered(doc, text: str) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(text)
    run.bold = True
    run.font.size = Pt(12)


# ---------------------------------------------------------------------------
# Bill of Lading (connaissement)
# ---------------------------------------------------------------------------


def build_bill_of_lading_docx(
    *, booking, leg, vessel, pol, pod, client, bl_number, issued_at=None
) -> DocxBytes:
    """Bill of Lading Word depuis un ``Booking`` confirmé.

    Reprend le contenu du BL PDF (``pdf/bill_of_lading.html``) : parties
    (shipper/carrier), voyage, marchandises, conditions La Haye-Visby, lieux
    de prise en charge/livraison, bloc de signature."""
    from app.templating import brand_for_lang

    brand: dict[str, Any] = brand_for_lang(getattr(client, "language", None) or "fr")
    issued = issued_at or datetime.now(UTC)
    doc = _new_document()

    _title(doc, "BILL OF LADING · CONNAISSEMENT")
    _ref_centered(doc, bl_number)
    doc.add_paragraph()

    # Parties (shipper / carrier)
    parties = doc.add_table(rows=1, cols=2)
    parties.style = "Table Grid"
    shipper = "\n".join(
        line
        for line in [
            client.company_name if client else "—",
            getattr(client, "contact_name", None) if client else None,
            client.email if client else None,
            getattr(client, "billing_address", None) if client else None,
            getattr(client, "country", None) if client else None,
        ]
        if line
    )
    carrier = "\n".join(
        [brand["raison_sociale"], brand["adresse"], brand["telephone"], brand["email"]]
    )
    parties.rows[0].cells[0].text = "Shipper · Expéditeur\n" + shipper
    parties.rows[0].cells[1].text = "Carrier · Transporteur\n" + carrier
    doc.add_paragraph()

    # Voyage
    _section(doc, "Voyage")
    vessel_desc = vessel.name
    extra = []
    if getattr(vessel, "imo_number", None):
        extra.append(f"IMO {vessel.imo_number}")
    if getattr(vessel, "flag", None):
        extra.append(vessel.flag)
    if extra:
        vessel_desc += f" ({vessel.code} · " + " · ".join(extra) + ")"
    else:
        vessel_desc += f" ({vessel.code})"
    _kv_table(
        doc,
        [
            ("Navire / Vessel", vessel_desc),
            ("Voyage · Leg code", leg.leg_code),
            ("Port of Loading (POL)", f"{pol.name} ({pol.locode} · {pol.country})"),
            ("Port of Discharge (POD)", f"{pod.name} ({pod.locode} · {pod.country})"),
            ("ETD", _fmt_date(leg.etd, "%d/%m/%Y %H:%M UTC")),
            ("ETA", _fmt_date(leg.eta, "%d/%m/%Y %H:%M UTC")),
        ],
    )
    doc.add_paragraph()

    # Marchandises
    _section(doc, "Goods · Marchandises")
    goods = doc.add_table(rows=1, cols=6)
    goods.style = "Table Grid"
    for idx, label in enumerate(
        ["Format", "Qté", "Description", "Poids unit. (kg)", "Poids total (kg)", "IMDG"]
    ):
        goods.rows[0].cells[idx].text = label
        if goods.rows[0].cells[idx].paragraphs[0].runs:
            goods.rows[0].cells[idx].paragraphs[0].runs[0].bold = True
    for item in booking.items:
        cells = goods.add_row().cells
        cells[0].text = item.pallet_format or "—"
        cells[1].text = str(item.pallet_count or 0)
        cells[2].text = item.cargo_description or "—"
        cells[3].text = str(item.unit_weight_kg) if item.unit_weight_kg is not None else "—"
        cells[4].text = str(item.total_weight_kg) if item.total_weight_kg is not None else "—"
        if getattr(item, "hazardous", False):
            imdg = item.imdg_class or "IMDG"
            if getattr(item, "un_number", None):
                imdg += f" · UN {item.un_number}"
            cells[5].text = imdg
        else:
            cells[5].text = "—"
    total_cells = goods.add_row().cells
    total_cells[0].text = "TOTAL"
    total_cells[0].paragraphs[0].runs[0].bold = True
    total_cells[4].text = str(booking.total_weight_kg)
    total_cells[5].text = f"{booking.total_palettes} palettes"
    doc.add_paragraph()

    # Conditions
    _section(doc, "Conditions")
    terms = booking.signed_terms_version or "v2026.1"
    cond = (
        "Transport assuré conformément aux Règles de La Haye-Visby. La "
        "responsabilité du transporteur est plafonnée selon les conventions "
        f"internationales en vigueur. Conditions générales applicables : {terms}"
    )
    if booking.signed_terms_at:
        cond += f", signées le {_fmt_date(booking.signed_terms_at)}"
    doc.add_paragraph(cond + ".")

    if booking.pickup_address or booking.delivery_address:
        _kv_table(
            doc,
            [
                row
                for row in [
                    (
                        ("Place of receipt", booking.pickup_address)
                        if booking.pickup_address
                        else None
                    ),
                    (
                        ("Place of delivery", booking.delivery_address)
                        if booking.delivery_address
                        else None
                    ),
                ]
                if row
            ],
        )

    # Bloc d'émission / signature
    doc.add_paragraph()
    stamp = doc.add_paragraph()
    stamp.add_run(f"Émis à {pol.name} le {_fmt_date(issued)}\n")
    stamp.add_run("Trois originaux signés (3 OBL)\n").italic = True
    stamp.add_run("\nCachet et signature du transporteur")

    _footer(doc)
    return _serialize(doc, f"{bl_number}.docx")
