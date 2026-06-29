"""Tests unitaires — wizard de conversion (logique pure, sans DB).

Couvre : grille des frais d'annulation (COM-08), garde des noms d'événements
analytics, signature du cookie d'appropriation du brouillon invité, et le
garde-fou anti open-redirect du paramètre ``next`` de connexion.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.booking import cancellation_fee_rate, compute_cancellation_fee


@pytest.mark.parametrize(
    "days,expected",
    [
        (60, "0"),
        (30, "0"),
        (29, "0.25"),
        (7, "0.25"),
        (6, "0.50"),
        (2, "0.50"),
        (1, "1.00"),
        (0, "1.00"),
        (None, "0"),
    ],
)
def test_cancellation_fee_rate(days, expected):
    assert cancellation_fee_rate(days) == Decimal(expected)


def test_compute_cancellation_fee_grid():
    price = Decimal("1000")
    assert compute_cancellation_fee(status="confirmed", price_eur=price, days_to_etd=40) == Decimal(
        "0.00"
    )
    assert compute_cancellation_fee(status="confirmed", price_eur=price, days_to_etd=10) == Decimal(
        "250.00"
    )
    assert compute_cancellation_fee(status="confirmed", price_eur=price, days_to_etd=5) == Decimal(
        "500.00"
    )
    assert compute_cancellation_fee(status="confirmed", price_eur=price, days_to_etd=1) == Decimal(
        "1000.00"
    )


def test_no_cancellation_fee_before_confirmation():
    # Annulation libre tant que la réservation n'est pas confirmée.
    for st in ("draft", "submitted"):
        assert (
            compute_cancellation_fee(status=st, price_eur=Decimal("1000"), days_to_etd=1)
            == Decimal("0")
        )


def test_cancellation_fee_handles_missing_price():
    assert compute_cancellation_fee(
        status="confirmed", price_eur=None, days_to_etd=1
    ) == Decimal("0")


def test_analytics_event_whitelist():
    from app.models.analytics_event import ANALYTICS_EVENTS

    for ev in ("landing_view", "quote_generated", "booking_submitted", "account_created"):
        assert ev in ANALYTICS_EVENTS


def test_draft_cookie_signature_roundtrip():
    from app.routers import booking_router as br

    token = br._sign_draft("BK-2026-ABCD")
    req_ok = SimpleNamespace(cookies={br._DRAFT_COOKIE: token})
    assert br._owns_draft(req_ok, "BK-2026-ABCD") is True
    # Mauvaise référence → refus.
    assert br._owns_draft(req_ok, "BK-2026-ZZZZ") is False
    # Cookie absent → refus.
    assert br._owns_draft(SimpleNamespace(cookies={}), "BK-2026-ABCD") is False
    # Cookie falsifié → refus.
    assert (
        br._owns_draft(SimpleNamespace(cookies={br._DRAFT_COOKIE: "forged"}), "BK-2026-ABCD")
        is False
    )


def test_login_next_is_safe_relative_only():
    from app.routers.client_auth_router import _safe_next

    assert _safe_next("/booking/BK-2026-ABCD/confirm") == "/booking/BK-2026-ABCD/confirm"
    assert _safe_next("/me") == "/me"
    # Open-redirect : refusés.
    assert _safe_next("//evil.example") is None
    assert _safe_next("https://evil.example") is None
    assert _safe_next("") is None
    assert _safe_next(None) is None
