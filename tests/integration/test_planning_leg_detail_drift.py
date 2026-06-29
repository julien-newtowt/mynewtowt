"""PLN-02 / PLN-05 — détail leg : exposition du réalisé (flux délégué) + dérive.

PLN-02 : le planificateur voit ATD/ATA & statut (réalisé) avec un renvoi
explicite au flux délégué (escale ATA/ATD, bord SOF) — pas de double saisie.
PLN-05 : la dérive planning (≥ 4 h vs `eta_ref`/`etd_ref`) est signalée.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.integration.conftest import FakeRequest
from tests.integration.test_mrv_reprise import _setup_leg


def test_leg_detail_template_exposes_delegated_flow_and_drift():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/planning/leg_detail.html")[0]
    # PLN-02 — flux délégué explicite
    assert "/escale" in src
    assert "Réalisé" in src
    # PLN-05 — indicateur de dérive
    assert "Dérive planning" in src
    assert "delayed" in src


@pytest.mark.asyncio
async def test_leg_detail_passes_delay_context(db, staff_user):
    from app.routers.planning_router import leg_detail

    leg = await _setup_leg(db)
    # ETA dérive de +6 h vs référence → leg en retard (seuil 4 h).
    leg.eta = leg.eta_ref + timedelta(hours=6)
    await db.flush()

    resp = await leg_detail(FakeRequest(), leg_id=leg.id, db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.context["delayed"] is True
    assert resp.context["delay_h"] >= 4
