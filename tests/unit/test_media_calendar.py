"""Tests unitaires — rétroplanning médias 2026–2027 (P12).

``build_moments`` est une fonction pure : on vérifie l'ordre chronologique des
4 livraisons de navires (Atlantis 07/2026, Atlas 09/2026, Archimedes 2027,
Astérias 2027) et l'intégration des arrivées café/cacao.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.vessel import Vessel
from app.services import media_calendar
from app.services.media_calendar import Arrival


def _vessel(name: str, delivery: str | None, status: str = "under_construction") -> Vessel:
    return Vessel(
        code=name[:4].upper(),
        name=name,
        build_status=status,
        expected_delivery=delivery,
    )


def test_four_deliveries_in_chronological_order():
    # Volontairement en désordre à l'entrée.
    vessels = [
        _vessel("Astérias", "2027"),
        _vessel("Atlantis", "2026-07"),
        _vessel("Archimedes", "2027"),
        _vessel("Atlas", "2026-09"),
    ]
    cal = media_calendar.build_moments(vessels, [], lang="fr")

    assert len(cal.moments) == 4
    assert [m.vessel_name for m in cal.deliveries] == [
        "Atlantis",
        "Atlas",
        "Archimedes",
        "Astérias",
    ]
    # Libellés localisés (mois FR pour le jeton daté, année seule sinon).
    assert cal.deliveries[0].date_label == "juillet 2026"
    assert cal.deliveries[1].date_label == "septembre 2026"
    assert cal.deliveries[2].date_label == "2027"
    assert "Atlantis" in cal.deliveries[0].title


def test_operational_vessels_are_excluded():
    vessels = [
        _vessel("Anemos", None, status="operational"),
        _vessel("Atlas", "2026-09"),
    ]
    cal = media_calendar.build_moments(vessels, [], lang="fr")
    assert [m.vessel_name for m in cal.deliveries] == ["Atlas"]


def test_unparseable_delivery_token_is_skipped():
    cal = media_calendar.build_moments([_vessel("Ghost", "soon")], [], lang="fr")
    assert cal.deliveries == ()


def test_arrivals_localized_and_interleaved_chronologically():
    vessels = [_vessel("Atlantis", "2026-07")]
    arrivals = [
        Arrival(
            leg_code="1AFRBR6",
            vessel_name="Anemos",
            port_name="Le Havre",
            arrival_at=datetime(2026, 3, 15, tzinfo=UTC),
            commodities=("coffee",),
            origin_labels=("Colombie",),
        )
    ]
    cal = media_calendar.build_moments(vessels, arrivals, lang="fr")

    assert len(cal.moments) == 2
    # L'arrivée de mars précède la livraison de juillet.
    assert cal.moments[0].kind == "cargo_arrival"
    assert cal.moments[1].kind == "vessel_delivery"
    arr = cal.arrivals[0]
    assert arr.date_label == "15/03/2026"
    assert "Café" in arr.title  # commodité localisée
    assert "Le Havre" in arr.detail and "Colombie" in arr.detail


def test_arrival_english_commodity_labels():
    arrivals = [
        Arrival(
            leg_code="2AFRBR6",
            vessel_name="Artemis",
            port_name="Le Havre",
            arrival_at=datetime(2026, 5, 1, tzinfo=UTC),
            commodities=("cacao", "coffee"),
            origin_labels=("Colombia", "Ecuador"),
        )
    ]
    cal = media_calendar.build_moments([], arrivals, lang="en")
    title = cal.arrivals[0].title
    assert "Cacao" in title and "Coffee" in title
