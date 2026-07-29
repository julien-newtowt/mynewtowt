"""ONB-08 — champs lieu / contexte de l'incident sur les sinistres (claims)."""

from __future__ import annotations

import pytest

from tests.integration.conftest import FakeRequest


def test_claim_model_has_incident_fields():
    from app.models.claim import Claim

    cols = Claim.__table__.columns.keys()
    assert "incident_location" in cols
    assert "incident_context" in cols


@pytest.mark.asyncio
async def test_claim_create_sets_incident_fields(db, staff_user):
    from sqlalchemy import select

    from app.models.claim import Claim
    from app.routers.claims_router import claim_create

    resp = await claim_create(
        FakeRequest(),
        title="Avarie cargo",
        description="desc",
        claim_type="other",
        occurred_at="2026-04-01T12:00:00",
        # Tous les paramètres optionnels doivent être passés explicitement :
        # appelée hors HTTP, la route reçoit sinon l'objet ``Form(None)`` par
        # défaut, qui finit lié en paramètre SQL (« type 'Form' is not supported »).
        leg_id=None,
        booking_id=None,
        crew_member_id=None,
        provision_eur=None,
        insurer=None,
        insurer_claim_ref=None,
        insurance_contract_id=None,
        cargo_position=None,
        incident_location="Port de Fécamp",
        incident_context="Choc à quai pendant la manutention",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303

    c = (await db.execute(select(Claim))).scalars().first()
    assert c.incident_location == "Port de Fécamp"
    assert c.incident_context == "Choc à quai pendant la manutention"


def test_new_claim_form_has_incident_fields():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/claims/new.html")[0]
    assert 'name="incident_location"' in src
    assert 'name="incident_context"' in src
