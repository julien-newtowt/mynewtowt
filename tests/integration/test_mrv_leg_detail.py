"""LOT 14 — archive lecture seule des ``mrv_events`` (remplace l'ancien détail leg).

L'ancien écran ``/mrv/legs/{leg_id}`` (``mrv_leg_detail``, table d'événements
éditable) a été RETIRÉ à la bascule. La consultation historique passe désormais
par ``/mrv/archive/events`` : paginée, lecture seule, bandeau explicite, aucune
action d'écriture. Ce fichier (jadis ``test_mrv_leg_detail``) vérifie la
nouvelle archive et l'absence de la route legacy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.integration.conftest import FakeRequest
from tests.integration.test_mrv_reprise import _ev, _setup_leg


def test_legacy_leg_detail_route_removed_archive_present():
    from app.routers import mrv_router

    paths = {r.path for r in mrv_router.router.routes}
    assert "/mrv/legs/{leg_id}" not in paths  # détail legacy retiré (lot 14)
    assert "/mrv/legs/{leg_id}/carbon" not in paths
    assert "/mrv/archive/events" in paths  # archive lecture seule


def test_archive_template_is_readonly_with_banner():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/mrv/archive_events.html")[0]
    # Bandeau « archive — remplacé par la capture d'événements ».
    assert "mrv_archive_banner_title" in src
    assert "mrv_archive_banner_body" in src
    # Lecture seule : aucun formulaire d'écriture (pas de suppression legacy).
    assert "<form" not in src
    assert "/mrv/events/" not in src


@pytest.mark.asyncio
async def test_archive_lists_events_readonly(db, staff_user):
    from app.routers.mrv_router import mrv_archive_events

    await _setup_leg(db)
    t = datetime(2026, 4, 2, 12, tzinfo=UTC)
    db.add(
        _ev(
            1,
            t,
            total_consumption_t=Decimal("1.500"),
            distance_nm=Decimal("100"),
            cargo_carried_t=Decimal("900"),
        )
    )
    db.add(_ev(1, t, kind="bunkering", bunkering_qty_t=Decimal("5.000")))
    db.add(
        _ev(
            1,
            t,
            total_consumption_t=Decimal("2.000"),
            distance_nm=Decimal("50"),
            quality_status="error",
            quality_notes="compteur incohérent",
        )
    )
    await db.flush()

    resp = await mrv_archive_events(FakeRequest(), page=1, db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.template.name == "staff/mrv/archive_events.html"
    assert resp.context["total"] == 3
    assert len(resp.context["events"]) == 3
    # Leg résolu pour l'affichage du code voyage.
    assert 1 in resp.context["leg_map"]
    assert resp.context["leg_map"][1].leg_code == "1CFRBR6"
    # Pagination : une seule page.
    assert resp.context["has_prev"] is False
    assert resp.context["has_next"] is False
