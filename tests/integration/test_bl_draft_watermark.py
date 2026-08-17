"""Le PDF doit dire ce qu'il est : draft, signé, ou final.

Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` §4.1.

Le filigrane n'est pas un habillage. Aujourd'hui le gabarit affirme, **quel que
soit l'état du lot** :

    Number of Original B/L : 3 (3 OBL signés)

et affiche une zone « Cachet et signature du transporteur ». Sur un draft que
personne n'a signé, ces deux mentions sont **fausses sur un document opposable** :
un tiers de bonne foi — banque en crédit documentaire, destinataire, assureur —
lit un connaissement original émis en trois exemplaires signés.

Ce que ces tests exigent :

1. un document non signé porte un **filigrane visible** et ne revendique **aucun**
   original signé ;
2. un document signé nomme **qui** a signé et **quand**, et ne porte plus de
   filigrane ;
3. la mention des 3 originaux (§5.1, « toujours 3 ») reste présente une fois
   signé — la correction ne doit pas supprimer une exigence métier.

Les tests portent sur le **HTML rendu**, pas sur le PDF : `DocumentBytes.html`
expose la source, ce qui les rend indépendants de WeasyPrint (GTK absent en local).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services import bl_workflow as w


def _batch(**kw):
    """Un lot minimal suffisant pour le gabarit BL."""
    base = {
        "id": 1,
        "batch_number": 1,
        "pallet_format": "EUR80x120",
        "pallet_count": 4,
        "weight_kg": 1200,
        "hs_code": "090111",
        "hazardous": False,
        "imdg_class": None,
        "un_number": None,
        "description_of_goods": "Café vert",
        "type_of_goods": None,
        "description": None,
        "marks_and_numbers": None,
        "shipper_name": "Belco",
        "shipper_address": None,
        "shipper_postal": None,
        "shipper_city": None,
        "shipper_country": None,
        "consignee_name": "Belco France",
        "consignee_address": None,
        "consignee_postal": None,
        "consignee_city": None,
        "consignee_country": None,
        "notify_name": None,
        "notify_address": None,
        "notify_postal": None,
        "notify_city": None,
        "notify_country": None,
        "bl_number": "TUAW_1_1",
        "bl_state": None,
        "bl_signed_at": None,
        "bl_signed_by_name": None,
        "bl_signature_hash": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _render(batch):
    """Rend le BL en remplaçant **seulement** la conversion WeasyPrint.

    Le gabarit réel et le contexte réellement construit par le service sont
    exercés : un `bl_state` oublié dans ce contexte ferait donc échouer les tests.
    Rendre le gabarit soi-même avec un contexte fabriqué à la main aurait au
    contraire rendu ces tests vides de sens.
    """
    import app.services.pdf_generator as gen
    from app.templating import templates

    def _html_only(template: str, context: dict) -> tuple[str, bytes]:
        return templates.get_template(template).render(**context), b"%PDF-1.4 fake"

    original = gen._render_pdf
    gen._render_pdf = _html_only
    try:
        doc = gen.render_bill_of_lading_from_pl(
            pl=SimpleNamespace(id=1, status="draft"),
            batch=batch,
            leg=None,
            vessel=None,
            pol=None,
            pod=None,
            bl_number=batch.bl_number,
            issued_at=datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
        )
    finally:
        gen._render_pdf = original
    return doc.html


# ───────────────────────── non signé ─────────────────────────


# On cible `class="bl-watermark"` et non `bl-watermark` : la seconde forme est
# aussi présente dans la feuille de style de `pdf/_base.html`, si bien qu'une
# assertion sur la chaîne nue serait vraie même sans filigrane rendu.
WATERMARK = 'class="bl-watermark"'


@pytest.mark.parametrize("state", [None, w.DRAFT, w.CLIENT_VALIDATED])
def test_an_unsigned_bl_is_watermarked(state):
    html = _render(_batch(bl_state=state))
    assert WATERMARK in html, f"aucun filigrane à l'état {state!r}"
    assert "DRAFT" in html


@pytest.mark.parametrize("state", [None, w.DRAFT, w.CLIENT_VALIDATED])
def test_an_unsigned_bl_claims_no_signed_original(state):
    """🔴 Le cœur du correctif : ne pas affirmer une signature inexistante."""
    html = _render(_batch(bl_state=state))
    assert "OBL signés" not in html, "le draft revendique des originaux signés"
    assert "Cachet et signature" not in html, "zone de signature sur un non-signé"


def test_the_unsigned_document_says_it_is_not_a_title():
    """Le filigrane doit être explicite sur la portée juridique, pas décoratif."""
    html = _render(_batch(bl_state=w.CLIENT_VALIDATED))
    low = html.lower()
    assert "sans valeur" in low or "ne vaut pas" in low or "non négociable" in low


# ───────────────────────── signé ─────────────────────────


def _signed(state=w.MASTER_SIGNED):
    return _batch(
        bl_state=state,
        bl_signed_at=datetime(2026, 8, 17, 12, 30, tzinfo=UTC),
        bl_signed_by_name="Cdt Le Bihan",
        bl_signature_hash="a" * 64,
    )


@pytest.mark.parametrize("state", [w.MASTER_SIGNED, w.FINAL])
def test_a_signed_bl_names_the_signatory_and_the_date(state):
    html = _render(_signed(state))
    assert "Cdt Le Bihan" in html, "le signataire n'est pas nommé"
    assert "17/08/2026" in html


@pytest.mark.parametrize("state", [w.MASTER_SIGNED, w.FINAL])
def test_a_signed_bl_carries_no_draft_watermark(state):
    html = _render(_signed(state))
    assert WATERMARK not in html
    assert "sans valeur de titre" not in html, "la mention « projet » survit à la signature"


def test_a_signed_bl_still_declares_three_originals():
    """⚠️ Garde anti-sur-correction : « toujours 3 » est une exigence métier (§5.1)."""
    html = _render(_signed())
    assert "OBL signés" in html
    assert "3" in html


def test_the_signature_fingerprint_is_printed_for_verification():
    """L'empreinte imprimée permet de confronter le papier au registre."""
    html = _render(_signed())
    assert "a" * 12 in html, "empreinte absente — le hash ne serait vérifiable par personne"


# ───────────────────────── nom du fichier ─────────────────────────


def _filename(batch):
    """Même substitution que `_render`, mais on veut ici le nom du document."""
    import app.services.pdf_generator as gen
    from app.templating import templates

    def _html_only(template: str, context: dict) -> tuple[str, bytes]:
        return templates.get_template(template).render(**context), b"%PDF-1.4 fake"

    original = gen._render_pdf
    gen._render_pdf = _html_only
    try:
        return gen.render_bill_of_lading_from_pl(
            pl=SimpleNamespace(id=1, status="draft"),
            batch=batch,
            leg=None,
            vessel=None,
            pol=None,
            pod=None,
            bl_number=batch.bl_number,
            issued_at=datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
        ).filename
    finally:
        gen._render_pdf = original


def test_the_draft_filename_says_draft():
    """Un PDF nommé comme un original finit par circuler comme un original."""
    assert _filename(_batch(bl_state=w.DRAFT)) == "TUAW_1_1-DRAFT.pdf"


def test_the_signed_filename_is_the_bl_number_alone():
    assert _filename(_signed()) == "TUAW_1_1.pdf"
