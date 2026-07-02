"""Intégration — P7 verticale « Cacao » (café-cacao uniquement).

Couvre : la page /solutions/cacao (rendu des récits, garde-fous, QR), la tuile
landing activée, la capture de lead segmentée (/contact?cargo=cacao) et le
référencement (sitemap / llms.txt). Vérifie aussi qu'aucune verticale
vin/spiritueux n'est construite (directive).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.i18n import t


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/solutions/cacao")
    state = SimpleNamespace(lang="fr")


# ───────────────────── page /solutions/cacao ─────────────────────


@pytest.mark.asyncio
async def test_solutions_cacao_renders():
    from app.routers.public_router import solutions_cacao

    resp = await solutions_cacao(_Req())
    assert resp.status_code == 200
    body = resp.body.decode()
    # Récits des trois origines cacao, titres injectés depuis le service.
    assert "Équateur" in body
    assert "Pérou" in body
    assert "République dominicaine" in body
    # Garde-fous doctrine : certificat nommé, aucun pourcentage.
    assert "certifié Anemos" in body
    assert "%" not in body.split("<body")[1]  # pas de % dans le corps rendu
    # CTA segmenté vers la capture de lead cacao.
    assert "/contact?cargo=cacao" in body
    # QR de vérification (dataviz CO₂) — SVG inline, data URI.
    assert "data:image/svg+xml;base64," in body


@pytest.mark.asyncio
async def test_solutions_cacao_hero_and_meta():
    from app.routers.public_router import solutions_cacao

    resp = await solutions_cacao(_Req())
    body = resp.body.decode()
    assert t("cacao_hero_title", "fr") in body
    assert t("cacao_s1_title", "fr") in body


# ───────────────────── landing : tuile activée, pas de vin ─────────────────────


@pytest.mark.asyncio
async def test_landing_activates_cocoa_tile(db):
    from app.routers.public_router import landing

    resp = await landing(_Req(), db=db)
    body = resp.body.decode()
    assert 'href="/solutions/cacao"' in body
    # La verticale vin/spiritueux n'est PAS activée (directive : cacao only).
    assert 'href="/solutions/vins-spiritueux"' not in body
    assert 'href="/solutions/vin"' not in body


# ───────────────────── capture de lead segmentée ─────────────────────


@pytest.mark.asyncio
async def test_contact_prefills_cargo_cacao():
    from app.routers.vitrine_router import contact_form

    resp = await contact_form(_Req(), cargo="cacao")
    body = resp.body.decode()
    assert 'value="Cacao / fèves"' in body


@pytest.mark.asyncio
async def test_contact_prefills_cargo_cafe():
    from app.routers.vitrine_router import contact_form

    resp = await contact_form(_Req(), cargo="cafe")
    body = resp.body.decode()
    assert 'value="Café vert"' in body


@pytest.mark.asyncio
async def test_contact_unknown_cargo_is_ignored():
    from app.routers.vitrine_router import contact_form

    # Valeur hors table → aucun reflet (pas d'injection de saisie utilisateur).
    resp = await contact_form(_Req(), cargo="<script>")
    body = resp.body.decode()
    assert "<script>" not in body.split("<body")[1]
    assert 'id="cargo_nature"' in body  # le champ existe, simplement vide


# ───────────────────── référencement ─────────────────────


def test_sitemap_and_llms_include_cacao():
    from app.services import seo

    paths = [p for (p, _f, _pr) in seo.PUBLIC_PAGES]
    assert "/solutions/cacao" in paths
    llms = seo.build_llms_txt("https://newtowt.eu")
    assert "/solutions/cacao" in llms
