"""PDF document generation for cargo workflow.

Generates Bill of Lading, Packing List, Invoice, and CO2 Certificate as
PDF bytes using WeasyPrint. Templates live in app/templates/pdf/* and
share a common Kairos brand stylesheet.

WeasyPrint is imported lazily at call time because it has heavy native
dependencies (Pango/Cairo); we don't want test imports to hard-fail when
WeasyPrint isn't installed in dev.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.config import settings
from app.services.co2 import estimate as estimate_co2
from app.templating import templates


@dataclass(frozen=True)
class DocumentBytes:
    """Wrapper carrying both the rendered HTML and the PDF bytes."""

    html: str
    pdf: bytes
    filename: str
    mime: str = "application/pdf"


_BRAND_CONTEXT = {
    "issued_at": None,  # set at render time
    "site_url": None,
}


def _render_pdf(template: str, context: dict[str, Any]) -> bytes:
    """Render a Jinja template then convert to PDF with WeasyPrint."""
    from weasyprint import HTML  # local import — heavy native deps

    tpl = templates.get_template(template)
    html = tpl.render(**context)
    return html, HTML(string=html, base_url=context.get("site_url", "")).write_pdf()


def _common_ctx(booking, leg, vessel, pol, pod, client) -> dict[str, Any]:
    return {
        "booking": booking,
        "leg": leg,
        "vessel": vessel,
        "pol": pol,
        "pod": pod,
        "client": client,
        "issued_at": datetime.now(UTC),
        "site_url": settings.site_url,
        # brand globals are already injected via templates.env.globals
    }


# ---------------------------------------------------------------------------
# Bill of Lading
# ---------------------------------------------------------------------------


def render_bill_of_lading(*, booking, leg, vessel, pol, pod, client) -> DocumentBytes:
    ctx = _common_ctx(booking, leg, vessel, pol, pod, client)
    ctx["bl_number"] = _bl_number(booking)
    html, pdf = _render_pdf("pdf/bill_of_lading.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename=f"BL_{booking.reference}.pdf")


def _bl_number(booking) -> str:
    """BL numbering convention from V2: TUAW_{leg_id}_{booking_id}."""
    return f"TUAW_{booking.leg_id}_{booking.id}"


def render_bill_of_lading_from_pl(
    *, pl, batch, leg, vessel, pol, pod, bl_number, issued_at=None
) -> DocumentBytes:
    """CARGO-01 — Bill of Lading généré depuis un batch de packing list.

    Les parties (shipper/consignee/notify) et la marchandise proviennent du
    ``PackingListBatch`` saisi par l'expéditeur (et non d'un booking).
    """
    from app.templating import brand_for_lang

    ctx = {
        "pl": pl,
        "batch": batch,
        "leg": leg,
        "vessel": vessel,
        "pol": pol,
        "pod": pod,
        "bl_number": bl_number,
        "issued_at": issued_at or datetime.now(UTC),
        "number_of_obl": 3,
        "brand": brand_for_lang("fr"),
        "site_url": settings.site_url,
    }
    html, pdf = _render_pdf("pdf/bill_of_lading_pl.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename=f"{bl_number}.pdf")


def render_arrival_notice(*, pl, batches, leg, vessel, pol, pod) -> DocumentBytes:
    """CARGO-05 — Avis d'arrivée (Arrival Notice) depuis la packing list."""
    from app.templating import brand_for_lang

    total_palettes = sum((b.pallet_count or 0) for b in batches)
    total_weight = sum((b.weight_kg or 0) for b in batches)
    ctx = {
        "pl": pl,
        "batches": batches,
        "leg": leg,
        "vessel": vessel,
        "pol": pol,
        "pod": pod,
        "total_palettes": total_palettes,
        "total_weight": total_weight,
        "issued_at": datetime.now(UTC),
        "brand": brand_for_lang("fr"),
        "site_url": settings.site_url,
    }
    html, pdf = _render_pdf("pdf/arrival_notice.html", ctx)
    ref = leg.leg_code if leg and leg.leg_code else pl.id
    return DocumentBytes(html=html, pdf=pdf, filename=f"ArrivalNotice_{ref}.pdf")


# ---------------------------------------------------------------------------
# Packing List
# ---------------------------------------------------------------------------


def render_packing_list(*, booking, leg, vessel, pol, pod, client) -> DocumentBytes:
    ctx = _common_ctx(booking, leg, vessel, pol, pod, client)
    html, pdf = _render_pdf("pdf/packing_list.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename=f"PackingList_{booking.reference}.pdf")


# ---------------------------------------------------------------------------
# Invoice (uses ClientInvoice if present, otherwise the booking estimate)
# ---------------------------------------------------------------------------


def render_invoice(*, booking, leg, vessel, pol, pod, client, invoice=None) -> DocumentBytes:
    ctx = _common_ctx(booking, leg, vessel, pol, pod, client)
    ctx["invoice"] = invoice
    ctx["amount_excl_vat"] = (
        invoice.amount_excl_vat_eur
        if invoice
        else (booking.confirmed_price_eur or booking.estimated_price_eur or Decimal("0"))
    )
    # Transport maritime international : exonéré de TVA (art. 262 II CGI) → 0 %.
    vat_rate = Decimal("0")
    if invoice:
        ctx["vat_rate"] = (
            (invoice.vat_amount_eur / invoice.amount_excl_vat_eur)
            if invoice.amount_excl_vat_eur
            else Decimal("0")
        )
        ctx["amount_incl_vat"] = invoice.amount_incl_vat_eur
        ctx["vat_amount"] = invoice.vat_amount_eur
    else:
        ctx["vat_rate"] = vat_rate
        ctx["vat_amount"] = (ctx["amount_excl_vat"] * vat_rate).quantize(Decimal("0.01"))
        ctx["amount_incl_vat"] = (ctx["amount_excl_vat"] + ctx["vat_amount"]).quantize(
            Decimal("0.01")
        )
    ctx["invoice_ref"] = invoice.reference if invoice else f"DEVIS-{booking.reference}"
    html, pdf = _render_pdf("pdf/invoice.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename=f"Invoice_{ctx['invoice_ref']}.pdf")


# ---------------------------------------------------------------------------
# Booking Note (COM-05) — confirme la réservation et ses conditions.
# La facturation est émise par la comptabilité NEWTOWT hors plateforme.
# ---------------------------------------------------------------------------


def render_booking_note(*, booking, leg, vessel, pol, pod, client) -> DocumentBytes:
    ctx = _common_ctx(booking, leg, vessel, pol, pod, client)
    ctx["note_ref"] = f"BN-{booking.reference}"
    ctx["price_eur"] = booking.confirmed_price_eur or booking.estimated_price_eur
    ctx["price_is_confirmed"] = booking.confirmed_price_eur is not None
    html, pdf = _render_pdf("pdf/booking_note.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename=f"BookingNote_{booking.reference}.pdf")


# ---------------------------------------------------------------------------
# Certificat Anemos (anciennement "Certificat CO₂") — PDF
# ---------------------------------------------------------------------------


def render_anemos_certificate(
    *,
    booking,
    leg,
    vessel,
    pol,
    pod,
    client,
    distance_nm: Decimal,
    certificate=None,
) -> DocumentBytes:
    """Génère un PDF Certificat Anemos.

    Le PDF atteste du tonnage transporté, distance, CO₂ évité par rapport
    au shipping conventionnel. Référence : ``ANEMOS-<booking.reference>``
    si pas de certificate.reference fournie.
    """
    tonnage = (booking.total_weight_kg or Decimal("0")) / Decimal("1000")
    emission = estimate_co2(distance_nm=distance_nm, tonnage_t=tonnage)
    ctx = _common_ctx(booking, leg, vessel, pol, pod, client)
    ctx["emission"] = emission
    ctx["certificate"] = certificate
    ctx["cert_ref"] = certificate.reference if certificate else f"ANEMOS-{booking.reference}"
    ctx["tonnage_t"] = tonnage
    # ENV-04 — QR de vérification publique : pointe vers /verify/{ref} (page
    # sans PII). Inline data-URI → pas d'appel réseau côté WeasyPrint.
    base = (settings.site_url or "").rstrip("/")
    ctx["verify_url"] = f"{base}/verify/{ctx['cert_ref']}"
    from app.services.mfa import qr_data_uri

    ctx["verify_qr"] = qr_data_uri(ctx["verify_url"])
    html, pdf = _render_pdf("pdf/anemos_certificate.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename=f"CertificatAnemos_{ctx['cert_ref']}.pdf")


def render_kit(
    *,
    booking,
    leg,
    vessel,
    pol,
    pod,
    client,
    cert,
    lang: str = "fr",
    co2_kg: int | None,
    story_long: str | None,
    story_short: str | None,
    client_logo_data: str | None = None,
    share_url: str | None = None,
) -> DocumentBytes:
    """Kit B2B2C par expédition (Vague 3) : récit d'origine + CO₂ évité figé +
    QR, co-brandé avec la marque du client. PDF téléchargeable.

    ``share_url`` (page publique de voyage ``/voyage/{ref}``, si publiée)
    prime sur l'URL de vérification pour la cible du QR : le consommateur
    scanne l'histoire complète, le certificat reste vérifiable depuis elle.
    """
    ctx = _common_ctx(booking, leg, vessel, pol, pod, client)
    base = (settings.site_url or "").rstrip("/")
    verify_url = f"{base}/verify/{cert.reference}" if cert else None
    qr_target = share_url or verify_url
    ctx.update(
        cert=cert,
        lang=lang,
        co2_kg=co2_kg,
        story_long=story_long,
        story_short=story_short,
        client_logo_data=client_logo_data,
        verify_url=verify_url,
        share_url=share_url,
        qr_target=qr_target,
    )
    if qr_target:
        from app.services.mfa import qr_data_uri

        ctx["verify_qr"] = qr_data_uri(qr_target)
    html, pdf = _render_pdf("pdf/kit.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename=f"KitB2B2C_{booking.reference}.pdf")


def render_planning_brochure(*, groups, summary, meta, lang: str = "fr") -> DocumentBytes:
    """PLN-01 — brochure commerciale imprimable du planning.

    ``groups`` est une liste de ``{"title": str, "rows": [{leg, vessel, pol, pod}]}``
    (vue chrono/route/destination construite côté routeur), ``summary`` un dict
    d'agrégats (nb legs, navires, période) et ``meta`` les filtres appliqués.
    """
    from app.templating import brand_for_lang

    ctx = {
        "groups": groups,
        "summary": summary,
        "meta": meta,
        "lang": lang,
        "brand": brand_for_lang(lang),
        "issued_at": datetime.now(UTC),
        "site_url": settings.site_url,
    }
    html, pdf = _render_pdf("pdf/planning_commercial.html", ctx)
    return DocumentBytes(html=html, pdf=pdf, filename="planning_newtowt.pdf")


# Alias backward-compat — peut disparaître en V3.7
render_co2_certificate = render_anemos_certificate
