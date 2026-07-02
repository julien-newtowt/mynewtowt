"""Intégration — P5 « preuve sociale » : compteurs réels, presse, kit presse.

Couvre :
- le service ``social_proof.counters`` (base vide → bandeau masqué ; données
  réelles → chiffres formatés) et sa doctrine (témoignages/logos vides tant
  que contenu + accord ne sont pas fournis) ;
- la landing (compteurs conditionnels, bandeau presse, balises og/twitter) ;
- le kit presse réel : /presse sans placeholder, pack logos ZIP, dossier PDF.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.i18n import t
from app.services import social_proof


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/")
    state = SimpleNamespace(lang="fr")


async def _seed_operations(db) -> None:
    from app.models.anemos_certificate import AnemosCertificate
    from app.models.booking import Booking
    from app.models.client_account import ClientAccount
    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel

    account = ClientAccount(email="c@example.test", hashed_password="x", company_name="C")
    vessel = Vessel(code="ANEM", name="Anemos")
    pol = Port(locode="BRSSO", name="São Sebastião", country="BR")
    pod = Port(locode="FRFEC", name="Fécamp", country="FR")
    db.add_all([account, vessel, pol, pod])
    await db.flush()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    # Une traversée réalisée (ATA posée) + une en cours (pas comptée).
    done = Leg(
        leg_code="1ABRFR6",
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base,
        eta_ref=base + timedelta(days=18),
        etd=base,
        eta=base + timedelta(days=18),
        atd=base,
        ata=base + timedelta(days=17),
    )
    ongoing = Leg(
        leg_code="2ABRFR6",
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base + timedelta(days=40),
        eta_ref=base + timedelta(days=58),
        etd=base + timedelta(days=40),
        eta=base + timedelta(days=58),
    )
    db.add_all([done, ongoing])
    await db.flush()
    # 1 200 palettes embarquées au total ; un brouillon jamais compté.
    db.add(
        Booking(
            reference="BK-1",
            leg_id=done.id,
            client_account_id=account.id,
            status="delivered",
            total_palettes=800,
        )
    )
    db.add(
        Booking(
            reference="BK-2",
            leg_id=ongoing.id,
            client_account_id=account.id,
            status="at_sea",
            total_palettes=400,
        )
    )
    db.add(
        Booking(
            reference="BK-3",
            leg_id=ongoing.id,
            client_account_id=account.id,
            status="draft",
            total_palettes=999,
        )
    )
    db.add(
        AnemosCertificate(
            reference="ANEMOS-BK-1",
            client_account_id=account.id,
            leg_id=done.id,
            tonnage_transported_t=Decimal("600"),
            distance_nm=Decimal("5150"),
            co2_emitted_kg=Decimal("8500"),
            co2_conventional_kg=Decimal("78000"),
            co2_avoided_kg=Decimal("69500"),
        )
    )
    await db.flush()


# ───────────────────── service compteurs ─────────────────────


@pytest.mark.asyncio
async def test_counters_empty_db_hides_band(db):
    social_proof.invalidate_counters_cache()
    counters = await social_proof.counters(db)
    assert counters.pallets == 0
    assert counters.co2_avoided_kg == 0
    assert counters.crossings == 0
    assert counters.has_content is False


@pytest.mark.asyncio
async def test_counters_computed_from_operations(db):
    social_proof.invalidate_counters_cache()
    await _seed_operations(db)
    counters = await social_proof.counters(db)
    assert counters.pallets == 1200  # delivered + at_sea, jamais les brouillons
    assert counters.crossings == 1  # seule la traversée avec ATA compte
    assert counters.co2_avoided_kg == 69500
    assert counters.pallets_str == "1 200"  # milliers à l'espace fine insécable
    assert counters.co2_str == "70 t"  # ≥ 1 000 kg → tonnes arrondies
    assert counters.has_content is True


def test_testimonials_and_logos_empty_by_default():
    """Doctrine : aucune preuve sociale nominative sans contenu + accord."""
    assert social_proof.TESTIMONIALS == ()
    assert social_proof.CLIENT_LOGOS == ()
    assert len(social_proof.PRESS_MENTIONS) >= 4
    assert all(m["url"].startswith("https://") for m in social_proof.PRESS_MENTIONS)


# ───────────────────── landing ─────────────────────


@pytest.mark.asyncio
async def test_landing_hides_counters_when_zero(db):
    from app.routers.public_router import landing

    social_proof.invalidate_counters_cache()
    resp = await landing(_Req(), db=db)
    assert resp.status_code == 200
    body = resp.body.decode()
    assert t("sp_counters_title", "fr") not in body
    # Le bandeau presse (fait public) reste affiché.
    assert t("sp_press_title", "fr") in body
    assert "Supply Chain Magazine" in body
    # Témoignages/logos absents tant que les listes sont vides.
    assert t("sp_testimonials_title", "fr") not in body


@pytest.mark.asyncio
async def test_landing_shows_counters_with_data(db):
    from app.routers.public_router import landing

    social_proof.invalidate_counters_cache()
    await _seed_operations(db)
    resp = await landing(_Req(), db=db)
    body = resp.body.decode()
    assert t("sp_counters_title", "fr") in body
    assert "1 200" in body
    assert "70 t" in body
    social_proof.invalidate_counters_cache()  # ne pas polluer les tests suivants


@pytest.mark.asyncio
async def test_landing_has_og_and_twitter_tags(db):
    from app.routers.public_router import landing

    social_proof.invalidate_counters_cache()
    resp = await landing(_Req(), db=db)
    body = resp.body.decode()
    assert 'property="og:image"' in body
    assert "/static/img/og-default.jpg" in body
    assert 'property="og:description"' in body
    assert 'name="twitter:card" content="summary_large_image"' in body


# ───────────────────── kit presse réel ─────────────────────


@pytest.mark.asyncio
async def test_presse_page_has_real_downloads_and_contact():
    from app.routers.vitrine_router import presse

    resp = await presse(_Req())
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "/presse/logos.zip" in body
    assert "/presse/dossier.pdf" in body
    assert "communication@towt.eu" in body
    assert "fichier à venir" not in body
    assert "à compléter" not in body


@pytest.mark.asyncio
async def test_presse_logos_zip(db):
    from app.routers.vitrine_router import _PREUVES_PDF_CACHE, presse_logos_zip

    _PREUVES_PDF_CACHE.clear()
    resp = await presse_logos_zip(_Req(), db=db)
    assert resp.status_code == 200
    assert resp.body.startswith(b"PK")
    names = zipfile.ZipFile(io.BytesIO(resp.body)).namelist()
    assert "NEWTOWT_logos/LISEZMOI.txt" in names
    assert sum(1 for n in names if n.endswith(".png")) >= 5


@pytest.mark.asyncio
async def test_presse_dossier_pdf(db):
    from app.routers.vitrine_router import _PREUVES_PDF_CACHE, presse_dossier_pdf

    _PREUVES_PDF_CACHE.clear()
    social_proof.invalidate_counters_cache()
    resp = await presse_dossier_pdf(_Req(), db=db)
    assert resp.status_code == 200
    assert resp.body.startswith(b"%PDF")
    assert "dossier_de_presse" in resp.headers["content-disposition"]
