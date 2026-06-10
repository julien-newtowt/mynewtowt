"""Auth primitives — hashing and session token signing."""
from __future__ import annotations

import time

import pytest
from itsdangerous import BadSignature

from app.auth import (
    AuthExpired,
    AuthInvalid,
    _decode,
    _staff_serializer,
    create_client_session,
    create_staff_session,
    hash_password,
    random_secret,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    h = hash_password("super-strong-12345")
    assert verify_password("super-strong-12345", h)
    assert not verify_password("other", h)


def test_random_secret_unique() -> None:
    a, b = random_secret(), random_secret()
    assert a != b
    assert len(a) > 20


def test_staff_session_decodes() -> None:
    token = create_staff_session(user_id=42)
    payload = _decode(token, _staff_serializer, max_age=3600)
    assert payload["uid"] == 42


def test_client_and_staff_tokens_are_separate_serializers() -> None:
    client_token = create_client_session(client_id=7)
    # Decoding client token with staff serializer must fail.
    with pytest.raises(AuthInvalid):
        _decode(client_token, _staff_serializer, max_age=3600)


def test_expired_token_raises() -> None:
    token = create_staff_session(user_id=42)
    # itsdangerous compares integer-seconds timestamps; sleep > 2s to ensure
    # the token is unambiguously beyond max_age=1.
    time.sleep(2.1)
    with pytest.raises(AuthExpired):
        _decode(token, _staff_serializer, max_age=1)
