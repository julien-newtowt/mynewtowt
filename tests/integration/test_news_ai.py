"""EVO-04 — couche IA de la veille (scoring affiné + digest).

Vérifie la **dégradation gracieuse** (sans clé → no-op), le parsing tolérant des
scores IA, et l'enrichissement cron idempotent (mocké).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _item(iid, title="Cargo à voile", desc="wind propulsion"):
    return SimpleNamespace(id=iid, title=title, description=desc, ai_score=None, is_archived=False)


@pytest.mark.asyncio
async def test_ai_relevance_no_key_returns_empty(monkeypatch):
    from app.config import settings
    from app.services import news_ai

    monkeypatch.setattr(settings, "anthropic_api_key", None, raising=False)
    assert await news_ai.ai_relevance(None, [_item(1), _item(2)]) == {}


@pytest.mark.asyncio
async def test_daily_digest_no_key_returns_none(monkeypatch):
    from app.config import settings
    from app.services import news_ai

    monkeypatch.setattr(settings, "anthropic_api_key", None, raising=False)
    assert await news_ai.daily_digest(None, [_item(1)]) is None


def test_parse_scores_clamps_and_filters():
    from app.services.news_ai import _parse_scores

    raw = 'Voici: {"1": 150, "2": -5, "3": 40, "99": 80, "x": 10}'
    out = _parse_scores(raw, valid_ids={1, 2, 3})
    assert out == {1: 100, 2: 0, 3: 40}  # clamp 0..100 ; id 99 hors lot ; "x" ignoré


def test_parse_scores_garbage_returns_empty():
    from app.services.news_ai import _parse_scores

    assert _parse_scores("pas de json ici", {1}) == {}


@pytest.mark.asyncio
async def test_ai_relevance_with_mocked_call(monkeypatch):
    from app.config import settings
    from app.services import news_ai

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test", raising=False)

    async def fake_call(system, payload):
        return '{"1": 90, "2": 10}'

    monkeypatch.setattr(news_ai, "_anthropic_text", fake_call)
    out = await news_ai.ai_relevance(None, [_item(1), _item(2)])
    assert out == {1: 90, 2: 10}


@pytest.mark.asyncio
async def test_enrich_after_ingest_no_key(monkeypatch):
    from app.config import settings
    from app.services import news_ai

    monkeypatch.setattr(settings, "anthropic_api_key", None, raising=False)
    report = await news_ai.enrich_after_ingest(None)
    assert report["scored"] == 0
    assert report["digest"] is False


def test_veille_template_shows_digest_and_ai_origin():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/veille/index.html")[0]
    assert "Synthèse du jour" in src
    assert "sc.ai" in src
