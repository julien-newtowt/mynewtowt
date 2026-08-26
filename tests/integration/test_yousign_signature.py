"""Signature électronique de la booking note (lot 7).

Ce qui est vérifié ici est d'abord une propriété de sécurité : **un webhook
forgé ne doit pas pouvoir faire passer une booking note pour signée**. La
confiance vient de l'empreinte HMAC du corps brut, et l'état métier n'est appliqué
qu'après relecture serveur-à-serveur.

Secure-by-default : sans clé, la voie électronique est indisponible et le
circuit manuscrit reste seul actif — jamais de repli silencieux vers « signé ».
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models.booking_note import BookingNote
from app.models.commercial import Client, RateOffer
from app.services import yousign as yousign_svc
from app.services.booking_note_signature import (
    SignatureError,
    apply_signature_state,
    request_signature,
)

_SECRET = "secret-webhook-de-test"


def _sign(payload: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


@pytest.fixture
def configured(monkeypatch):
    """Instance où Yousign est provisionné."""
    monkeypatch.setattr(yousign_svc.settings, "yousign_api_key", "clef-api", raising=False)
    monkeypatch.setattr(
        yousign_svc.settings, "yousign_webhook_secret", _SECRET, raising=False
    )


@pytest.fixture
def not_configured(monkeypatch):
    monkeypatch.setattr(yousign_svc.settings, "yousign_api_key", None, raising=False)
    monkeypatch.setattr(yousign_svc.settings, "yousign_webhook_secret", None, raising=False)


async def _note(db, *, status="diffusee", signature_status=None) -> BookingNote:
    client = Client(name="Cacao Négoce", client_type="shipper")
    db.add(client)
    await db.flush()
    offer = RateOffer(
        reference="RO-2026-0001",
        client_id=client.id,
        title="Transat cacao",
        status="valide",
        valid_until=date(2026, 12, 31),
    )
    db.add(offer)
    await db.flush()
    note = BookingNote(
        offer_id=offer.id,
        reference="BN-2026-0001",
        status=status,
        merchant_email="ops@cacao-negoce.fr",
        merchant_contact="Marie Dupont",
        signature_status=signature_status,
        signature_request_id="sr_test_1" if signature_status else None,
    )
    db.add(note)
    await db.flush()
    return note


# ───────────────────────── Vérification de signature ─────────────────────


def test_valid_signature_is_accepted(configured):
    payload = b'{"event_name":"signature_request.done"}'
    assert yousign_svc.verify_webhook_signature(payload, _sign(payload)) is True
    # Forme préfixée tolérée.
    assert yousign_svc.verify_webhook_signature(payload, f"sha256={_sign(payload)}") is True


def test_forged_or_missing_signature_is_rejected(configured):
    payload = b'{"event_name":"signature_request.done"}'
    assert yousign_svc.verify_webhook_signature(payload, "0" * 64) is False
    assert yousign_svc.verify_webhook_signature(payload, None) is False
    assert yousign_svc.verify_webhook_signature(payload, "") is False


def test_signature_of_another_payload_is_rejected(configured):
    """Rejouer une empreinte valide sur un autre corps ne passe pas."""
    legit = b'{"event_name":"signature_request.expired"}'
    forged = b'{"event_name":"signature_request.done"}'
    assert yousign_svc.verify_webhook_signature(forged, _sign(legit)) is False


def test_signature_with_the_wrong_secret_is_rejected(configured):
    payload = b'{"event_name":"signature_request.done"}'
    assert yousign_svc.verify_webhook_signature(payload, _sign(payload, "mauvais")) is False


def test_verification_refuses_to_run_without_a_secret(not_configured):
    with pytest.raises(yousign_svc.YousignNotConfigured):
        yousign_svc.verify_webhook_signature(b"{}", "peu importe")


# ───────────────────────── Secure-by-default ─────────────────────────


@pytest.mark.asyncio
async def test_signature_request_is_refused_without_a_key(db, not_configured):
    note = await _note(db)
    with pytest.raises(SignatureError, match="non configurée"):
        await request_signature(db, note, document=b"PK", filename="bn.docx")
    assert note.signature_status is None


@pytest.mark.asyncio
async def test_a_draft_cannot_be_sent_to_signature(db, configured):
    """Signer un document encore modifiable viderait la signature de son sens."""
    note = await _note(db, status="brouillon")
    with pytest.raises(SignatureError, match="[Dd]iffusez"):
        await request_signature(db, note, document=b"PK", filename="bn.docx")


@pytest.mark.asyncio
async def test_signature_request_needs_a_signer_email(db, configured):
    note = await _note(db)
    note.merchant_email = None
    await db.flush()
    with pytest.raises(SignatureError, match="e-mail"):
        await request_signature(db, note, document=b"PK", filename="bn.docx")


# ────────────── L'état métier ne vient jamais du payload ──────────────


@pytest.mark.asyncio
async def test_done_event_not_confirmed_by_yousign_does_not_sign(db, configured, monkeypatch):
    """Le webhook annonce « signé », l'API dit le contraire : on ne signe pas."""
    note = await _note(db, signature_status="requested")

    async def _remote(_request_id):
        return {"status": "ongoing"}

    monkeypatch.setattr(yousign_svc, "retrieve_signature_request", _remote)
    await apply_signature_state(db, note, event_type="signature_request.done")
    assert note.signature_status == "requested"
    assert note.signed_at is None


@pytest.mark.asyncio
async def test_done_event_confirmed_by_yousign_signs(db, configured, monkeypatch):
    note = await _note(db, signature_status="requested")

    async def _remote(_request_id):
        return {"status": "done"}

    async def _download(_request_id):
        return b"%PDF-1.7 signe"

    monkeypatch.setattr(yousign_svc, "retrieve_signature_request", _remote)
    monkeypatch.setattr(yousign_svc, "download_signed_document", _download)

    await apply_signature_state(db, note, event_type="signature_request.done")
    assert note.signature_status == "signed"
    assert note.signed_at is not None
    assert note.signed_document_sha256 == hashlib.sha256(b"%PDF-1.7 signe").hexdigest()


@pytest.mark.asyncio
async def test_a_signed_note_is_never_downgraded(db, configured, monkeypatch):
    """Un événement d'expiration arrivé en retard n'efface pas une signature acquise."""
    note = await _note(db, signature_status="signed")
    note.signed_at = datetime.now(UTC)
    await db.flush()

    called = False

    async def _remote(_request_id):
        nonlocal called
        called = True
        return {"status": "expired"}

    monkeypatch.setattr(yousign_svc, "retrieve_signature_request", _remote)
    await apply_signature_state(db, note, event_type="signature_request.expired")
    assert note.signature_status == "signed"
    assert called is False  # on n'interroge même pas : l'état est terminal


@pytest.mark.asyncio
async def test_replaying_the_same_event_changes_nothing(db, configured, monkeypatch):
    note = await _note(db, signature_status="requested")

    async def _remote(_request_id):
        return {"status": "declined"}

    monkeypatch.setattr(yousign_svc, "retrieve_signature_request", _remote)
    await apply_signature_state(db, note, event_type="signature_request.declined")
    assert note.signature_status == "declined"
    await apply_signature_state(db, note, event_type="signature_request.declined")
    assert note.signature_status == "declined"


@pytest.mark.asyncio
async def test_a_failed_download_does_not_cancel_the_signature(db, configured, monkeypatch):
    """La signature est acquise chez Yousign : l'archive se rattrape, elle ne bloque pas."""
    note = await _note(db, signature_status="requested")

    async def _remote(_request_id):
        return {"status": "done"}

    async def _download(_request_id):
        raise yousign_svc.YousignError("503")

    monkeypatch.setattr(yousign_svc, "retrieve_signature_request", _remote)
    monkeypatch.setattr(yousign_svc, "download_signed_document", _download)

    await apply_signature_state(db, note, event_type="signature_request.done")
    assert note.signature_status == "signed"
    assert note.signed_document_sha256 is None


# ───────────────────────────── Webhook HTTP ─────────────────────────────


@pytest.mark.asyncio
async def test_webhook_rejects_a_forged_signature(db, configured):
    from app.routers.yousign_router import yousign_webhook

    body = json.dumps(
        {"event_name": "signature_request.done", "data": {"signature_request": {"id": "sr_x"}}}
    ).encode()

    class _Req:
        headers = {"x-yousign-signature": "0" * 64}

        async def body(self):
            return body

        async def json(self):
            return json.loads(body)

    response = await yousign_webhook(_Req(), db=db)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_webhook_returns_503_without_a_secret(db, not_configured):
    from app.routers.yousign_router import yousign_webhook

    class _Req:
        headers: dict[str, str] = {}

        async def body(self):
            return b"{}"

    response = await yousign_webhook(_Req(), db=db)
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_webhook_acknowledges_unknown_events_without_failing(db, configured):
    """Répondre 500 ferait désactiver l'endpoint chez Yousign après retries."""
    from app.routers.yousign_router import yousign_webhook

    raw = json.dumps({"event_name": "signature_request.reminder_executed"}).encode()

    class _Req:
        headers = {"x-yousign-signature": _sign(raw)}

        async def body(self):
            return raw

        async def json(self):
            return json.loads(raw)

    response = await yousign_webhook(_Req(), db=db)
    assert response.status_code == 200


def test_name_split_never_sends_an_empty_field():
    assert yousign_svc.split_name("Marie Dupont") == ("Marie", "Dupont")
    assert yousign_svc.split_name("Jean-Pierre de La Tour") == ("Jean-Pierre de La", "Tour")
    assert yousign_svc.split_name("Marie") == ("Marie", "—")
    assert yousign_svc.split_name(None, "ops@acme.fr") == ("ops", "—")
    assert yousign_svc.split_name("   ", None) == ("Contact", "—")


def test_api_token_never_travels_in_the_url(configured):
    """Le jeton passe en en-tête : une URL complète est journalisée partout."""
    headers = yousign_svc._headers()
    assert headers["Authorization"] == "Bearer clef-api"
    assert "clef-api" not in yousign_svc.API_BASE


def test_settings_expose_a_disabled_flag_by_default():
    """Sans clé, ``yousign_enabled`` est faux — la voie électronique est fermée."""
    from app.config import Settings

    settings = Settings(
        secret_key="x" * 40, database_url="postgresql+asyncpg://u:p@localhost/db"
    )
    assert settings.yousign_enabled is False


def test_signature_and_payment_stay_independent():
    """Signer n'est pas régler : aucun état de signature ne vaut encaissement."""
    from app.services.booking_note_signature import SIGNATURE_STATUSES

    assert not any(
        token in status
        for status in SIGNATURE_STATUSES
        for token in ("paid", "regle", "payment")
    )
    note = SimpleNamespace(signature_status="signed", document_sha256=None)
    assert not hasattr(note, "paid_at")
