"""Tests du décalage d'escale selon les jours fermés au commerce (planning)."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services.planning import next_working_departure

# Repère : 2026-06-12 = vendredi, 13 = samedi, 14 = dimanche, 15 = lundi.
FRI = datetime(2026, 6, 12, 8, 0)
SAT = datetime(2026, 6, 13, 8, 0)
SUN = datetime(2026, 6, 14, 8, 0)
SAT_SUN = {5, 6}


def test_no_closed_days_is_simple_addition() -> None:
    out = next_working_departure(FRI, 24, set())
    assert out == FRI + timedelta(hours=24)


def test_arrival_on_saturday_shifts_to_monday() -> None:
    # Arrivée samedi, port fermé WE → opérations démarrent lundi.
    out = next_working_departure(SAT, 24, SAT_SUN)
    # start = lundi 15 08:00 ; +24h = mardi 16 08:00 (pas de jour fermé traversé).
    assert out == datetime(2026, 6, 16, 8, 0)


def test_stay_spanning_weekend_is_extended() -> None:
    # Arrivée vendredi 08:00, escale 24h : le départ naïf tomberait samedi.
    # Samedi+dimanche fermés → +2 jours, départ lundi 08:00.
    out = next_working_departure(FRI, 24, SAT_SUN)
    assert out == datetime(2026, 6, 15, 8, 0)


def test_departure_never_lands_on_closed_day() -> None:
    # Arrivée vendredi, escale très courte mais départ repoussé hors WE si besoin.
    out = next_working_departure(FRI, 2, SAT_SUN)
    assert out.weekday() not in SAT_SUN


def test_only_sunday_closed() -> None:
    # Port fermé uniquement le dimanche : arrivée samedi → opère samedi.
    out = next_working_departure(SAT, 2, {6})
    assert out == SAT + timedelta(hours=2)
    # Mais une arrivée dimanche glisse à lundi.
    out2 = next_working_departure(SUN, 2, {6})
    assert out2.weekday() == 0  # lundi
