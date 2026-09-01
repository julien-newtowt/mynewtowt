"""Dates effectives (réel prioritaire) — Gantt, helpers, suggestions.

Doctrine couverte ici : tout affichage « où en est le voyage » lit le RÉEL
(ATD/ATA) dès qu'il est posé, et retombe sur le prévisionnel (ETD/ETA) sinon.
Le défaut corrigé : le Gantt — outil de décision de l'exploitation — dessinait
les barres sur l'ETD/ETA même sur un navire déjà appareillé, donc affichait un
planning que plus personne ne suivait.

Trois volets :
  1. ``effective_etd`` / ``effective_eta`` : priorité au réel, repli
     prévisionnel, tz-safe (SQLite relit naïf, les helpers rendent aware).
  2. ``_build_gantt_rows`` : position ET longueur de barre sur les dates
     effectives, y compris le cas voulu « parti mais pas arrivé ».
  3. ``_new_leg_suggestions`` : format ``%Y-%m-%d`` (le formulaire est passé
     à ``<input type="date">`` — un ``…THH:MM`` serait ignoré par le
     navigateur et la suggestion ne s'appliquerait pas).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.leg import Leg
from app.models.vessel import Vessel
from app.routers.planning_router import _build_gantt_rows, _new_leg_suggestions
from app.services.planning import effective_eta, effective_etd, ensure_utc
from tests.integration.test_mrv_reprise import _setup_leg

# ``_setup_leg`` pose etd = 2026-04-01, eta = +20 j (navire 1, FRFEC → BRSSO).
BASE = datetime(2026, 4, 1, tzinfo=UTC)
WINDOW_START = datetime(2026, 1, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)


def _pct(moment: datetime) -> float:
    """Pourcentage attendu d'un instant sur la fenêtre du Gantt (même formule)."""
    total = (WINDOW_END - WINDOW_START).total_seconds()
    return round(((moment - WINDOW_START).total_seconds() / total) * 100, 3)


async def _gantt_bars(db) -> list[dict]:
    """Barres du Gantt sur l'année 2026, comme la route ``GET /planning``."""
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    legs = list((await db.execute(select(Leg))).scalars().all())
    rows = _build_gantt_rows(
        vessels=vessels,
        legs=legs,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        ports={},
        conflict_ids=set(),
    )
    return [bar for row in rows for bar in row["bars"]]


# ───────────────────────── helpers canoniques ─────────────────────────


@pytest.mark.asyncio
async def test_effective_dates_fall_back_to_forecast(db):
    """Sans réel déclaré, les helpers rendent le prévisionnel (aware UTC)."""
    leg = await _setup_leg(db)
    assert leg.atd is None and leg.ata is None
    assert effective_etd(leg) == BASE
    assert effective_eta(leg) == BASE + timedelta(days=20)
    # tz-safe : comparable sans lever face à une borne aware, même relu naïf.
    assert effective_etd(leg).tzinfo is not None
    assert effective_etd(leg) > WINDOW_START


@pytest.mark.asyncio
async def test_effective_dates_prefer_actuals(db):
    """ATD/ATA posés : le réel prime, l'un indépendamment de l'autre."""
    leg = await _setup_leg(db)
    leg.atd = BASE + timedelta(days=2)
    await db.flush()
    # Départ réel pris en compte ; arrivée encore inconnue → ETA prévisionnelle.
    assert effective_etd(leg) == BASE + timedelta(days=2)
    assert effective_eta(leg) == BASE + timedelta(days=20)

    leg.ata = BASE + timedelta(days=23)
    await db.flush()
    assert effective_eta(leg) == BASE + timedelta(days=23)
    # Le prévisionnel n'est pas réécrit : les deux registres coexistent.
    assert ensure_utc(leg.etd) == BASE
    assert ensure_utc(leg.eta) == BASE + timedelta(days=20)


# ─────────────────────────── barres du Gantt ───────────────────────────


@pytest.mark.asyncio
async def test_gantt_bar_positioned_on_atd_not_etd(db):
    """Un leg appareillé se dessine sur son ATD, pas sur son ETD."""
    leg = await _setup_leg(db)
    leg.atd = BASE + timedelta(days=2)
    leg.ata = BASE + timedelta(days=23)
    await db.flush()

    bars = await _gantt_bars(db)
    assert len(bars) == 1
    bar = bars[0]

    expected_left = _pct(BASE + timedelta(days=2))
    assert bar["left_pct"] == pytest.approx(expected_left, abs=0.01)
    # Et surtout : ce n'est PAS la position de l'ETD (le défaut corrigé).
    assert bar["left_pct"] != pytest.approx(_pct(BASE), abs=0.01)
    # Longueur = durée réelle (21 j), pas la durée planifiée (20 j).
    assert bar["width_pct"] == pytest.approx(
        _pct(BASE + timedelta(days=23)) - expected_left, abs=0.01
    )
    # Infobulle alignée sur la géométrie de la barre.
    assert bar["etd"] == BASE + timedelta(days=2)
    assert bar["eta"] == BASE + timedelta(days=23)


@pytest.mark.asyncio
async def test_gantt_bar_sailed_but_not_arrived_keeps_forecast_eta(db):
    """Parti sans être arrivé : ATD réel → ETA prévisionnelle (comportement voulu)."""
    leg = await _setup_leg(db)
    leg.atd = BASE + timedelta(days=2)
    await db.flush()

    bar = (await _gantt_bars(db))[0]
    assert bar["left_pct"] == pytest.approx(_pct(BASE + timedelta(days=2)), abs=0.01)
    assert bar["eta"] == BASE + timedelta(days=20)
    # La barre raccourcit du retard au départ : l'arrivée annoncée n'a pas bougé.
    assert bar["width_pct"] == pytest.approx(
        _pct(BASE + timedelta(days=20)) - _pct(BASE + timedelta(days=2)), abs=0.01
    )


@pytest.mark.asyncio
async def test_gantt_bar_without_actuals_uses_forecast(db):
    """Aucun réel déclaré : la barre reste sur l'ETD/ETA (non-régression)."""
    await _setup_leg(db)
    bar = (await _gantt_bars(db))[0]
    assert bar["left_pct"] == pytest.approx(_pct(BASE), abs=0.01)
    assert bar["etd"] == BASE and bar["eta"] == BASE + timedelta(days=20)


@pytest.mark.asyncio
async def test_gantt_clamps_to_window_and_skips_legs_outside(db):
    """Clamps de fenêtre préservés : hors année affichée, pas de barre."""
    leg = await _setup_leg(db)
    # Le leg glisse entièrement sur 2027 via son réel → hors fenêtre 2026.
    leg.atd = datetime(2027, 2, 1, tzinfo=UTC)
    leg.ata = datetime(2027, 2, 20, tzinfo=UTC)
    await db.flush()
    assert await _gantt_bars(db) == []

    # À cheval sur la borne haute : la barre est tronquée, pas supprimée.
    leg.atd = datetime(2026, 12, 20, tzinfo=UTC)
    leg.ata = datetime(2027, 1, 10, tzinfo=UTC)
    await db.flush()
    bar = (await _gantt_bars(db))[0]
    assert bar["left_pct"] == pytest.approx(_pct(datetime(2026, 12, 20, tzinfo=UTC)), abs=0.01)
    assert bar["left_pct"] + bar["width_pct"] <= 100.01


# ──────────────────── suggestions du formulaire (jour) ────────────────────


@pytest.mark.asyncio
async def test_new_leg_suggestions_are_date_only(db):
    """Contrat avec ``<input type="date">`` : jour seul, sans partie horaire."""
    leg = await _setup_leg(db)
    leg.ata = BASE + timedelta(days=23)
    await db.flush()

    suggestions = await _new_leg_suggestions(db)
    sug = suggestions[1]
    assert sug["etd"] == "2026-04-26"  # ATA (24/04) + 48 h d'escale par défaut
    assert sug["from_ata"] == "2026-04-24"
    assert sug["from_eta"] == "2026-04-21"
    assert "T" not in sug["etd"]
    assert sug["pol_id"] == leg.arrival_port_id  # continuité géographique
