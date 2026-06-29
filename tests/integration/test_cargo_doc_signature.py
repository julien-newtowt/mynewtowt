"""EVO-09 — signatures IMO appliquées aux documents cargo (ONB-02).

Vérifie que la signature pose le hash + le verrou (comme SOF/noon/watch), que le
hash est vérifiable, et qu'un document signé n'est plus éditable ni re-signable
(409 via ``ensure_unlocked`` / ``sign_record``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from tests.integration.conftest import FakeRequest
from tests.integration.test_mrv_reprise import _setup_leg


@pytest.mark.asyncio
async def test_sign_cargo_document_locks_and_hashes(db, staff_user):
    from app.models.sof_event import CargoDocument
    from app.routers.captain_router import sign_cargo_document, update_cargo_document
    from app.services.signature import compute_cargo_doc_hash, verify_hash

    await _setup_leg(db)
    doc = CargoDocument(
        leg_id=1, kind="NOR", reference="NOR-1", issued_at=datetime(2026, 4, 1, tzinfo=UTC)
    )
    db.add(doc)
    await db.flush()

    resp = await sign_cargo_document(1, doc.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert doc.is_locked is True
    assert doc.signed_by_name
    assert doc.signature_hash
    assert verify_hash(doc, hash_fn=compute_cargo_doc_hash) is True

    # Un document signé n'est plus éditable.
    with pytest.raises(HTTPException) as exc_edit:
        await update_cargo_document(
            1, doc.id, FakeRequest(form={"reference": "X"}), db=db, user=staff_user
        )
    assert exc_edit.value.status_code == 409

    # Ni re-signable.
    with pytest.raises(HTTPException) as exc_sign:
        await sign_cargo_document(1, doc.id, FakeRequest(), db=db, user=staff_user)
    assert exc_sign.value.status_code == 409


def test_captain_template_has_sign_button():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/captain/index.html")[0]
    assert "/sign" in src
    assert "Signer" in src
