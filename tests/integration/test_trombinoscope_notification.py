"""Intégration — notification in-app à la génération du trombinoscope.

Cf. docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md (module TRB-5).
"""

from __future__ import annotations

import pytest

from app.services import notifications


@pytest.mark.asyncio
async def test_notify_trombinoscope_generated_targets_armement_role(db):
    n = await notifications.notify_trombinoscope_generated(db, period="2026-07")

    assert n.type == "trombinoscope_generated"
    assert n.target_role == "armement"
    assert n.target_user_id is None
    assert n.link == "/crew/trombinoscope.pdf"
    assert "2026-07" in n.title


@pytest.mark.asyncio
async def test_notify_trombinoscope_generated_visible_to_armement_role(db):
    await notifications.notify_trombinoscope_generated(db, period="2026-08")

    rows = await notifications.list_for(db, user_role="armement")
    assert any(r.type == "trombinoscope_generated" for r in rows)
