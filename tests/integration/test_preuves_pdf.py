"""Intégration — P3 « conformité des claims » : téléchargements réels de /preuves.

Couvre :
- la méthodologie Anemos en PDF réel (WeasyPrint, facteurs courants, cache) ;
- le spécimen du rapport CO₂ annuel (données fictives cohérentes, SPÉCIMEN) ;
- le rate-limit des routes PDF publiques ;
- la page /preuves qui pointe vers les vrais fichiers (fin des liens factices).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.routers import vitrine_router
from app.routers.vitrine_router import (
    _PREUVES_PDF_CACHE,
    _specimen_report,
    preuves,
    preuves_methodology_pdf,
    preuves_sample_annual_report_pdf,
)


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/preuves")
    state = SimpleNamespace(lang="fr")


def _req(lang: str = "fr", host: str = "127.0.0.1") -> _Req:
    r = _Req()
    r.client = SimpleNamespace(host=host)
    r.state = SimpleNamespace(lang=lang)
    return r


# ───────────────────── méthodologie PDF ─────────────────────


@pytest.mark.asyncio
async def test_methodology_pdf_renders_and_caches(db):
    _PREUVES_PDF_CACHE.clear()
    resp = await preuves_methodology_pdf(_req(), db=db)
    assert resp.status_code == 200
    assert resp.media_type == "application/pdf"
    assert resp.body.startswith(b"%PDF")
    assert len(_PREUVES_PDF_CACHE) == 1

    # Second appel : servi depuis le cache (mêmes octets, pas de 2e entrée).
    resp2 = await preuves_methodology_pdf(_req(), db=db)
    assert resp2.body == resp.body
    assert len(_PREUVES_PDF_CACHE) == 1

    # La langue fait partie de la clé de contenu (document EN distinct).
    resp_en = await preuves_methodology_pdf(_req(lang="en"), db=db)
    assert resp_en.body.startswith(b"%PDF")
    assert len(_PREUVES_PDF_CACHE) == 2
    assert "_en.pdf" in resp_en.headers["content-disposition"]


# ───────────────────── spécimen rapport annuel ─────────────────────


def test_specimen_report_is_coherent_with_published_formula():
    report = _specimen_report()
    assert report["shipment_count"] == 3
    assert report["declared_count"] == 2
    # Totaux = somme des lignes ; évité = référence − émis (formule publiée).
    assert report["total_avoided_kg"] == sum(s["co2_avoided_kg"] for s in report["shipments"])
    assert (
        report["total_avoided_kg"] == report["total_conventional_kg"] - report["total_emitted_kg"]
    )
    # Vérification par recalcul indépendant sur la 1re ligne :
    # (13,7 − 1,5) × 18,4 t × (5150 NM × 1,852) km ÷ 1000 ≈ 2141 kg.
    first = report["shipments"][0]
    expected = (
        (Decimal("13.7") - Decimal("1.5"))
        * Decimal("18.4")
        * (Decimal("5150") * Decimal("1.852"))
        / 1000
    )
    assert abs(first["co2_avoided_kg"] - expected) < 2  # arrondis au kg


@pytest.mark.asyncio
async def test_specimen_pdf_renders(db):
    _PREUVES_PDF_CACHE.clear()
    resp = await preuves_sample_annual_report_pdf(_req(), db=db)
    assert resp.status_code == 200
    assert resp.body.startswith(b"%PDF")
    assert "SPECIMEN" in resp.headers["content-disposition"]


# ───────────────────── rate-limit ─────────────────────


@pytest.mark.asyncio
async def test_preuves_pdf_rate_limited(db):
    from fastapi import HTTPException

    _PREUVES_PDF_CACHE.clear()
    # 1er appel : rend le PDF (cache) ; les suivants sont servis du cache
    # jusqu'à épuisement du quota (20/10 min), puis 429.
    for _ in range(vitrine_router._PREUVES_PDF_RATE_MAX):
        await preuves_methodology_pdf(_req(host="10.9.9.9"), db=db)
    with pytest.raises(HTTPException) as exc:
        await preuves_methodology_pdf(_req(host="10.9.9.9"), db=db)
    assert exc.value.status_code == 429


# ───────────────────── page /preuves ─────────────────────


@pytest.mark.asyncio
async def test_preuves_page_links_to_real_downloads():
    resp = await preuves(_req())
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "/preuves/methodologie.pdf" in body
    assert "/preuves/rapport-annuel-exemple.pdf" in body
    # Le lien factice historique (bouton méthodologie → page HTML) a disparu :
    # le bouton principal pointe vers le PDF réel.
    assert "Télécharger la méthodologie (PDF)" in body
