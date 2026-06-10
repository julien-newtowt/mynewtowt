"""Unit tests — service de demande de cotation/contact (validation pure)."""

from __future__ import annotations

import pytest

from app.services.contact import (
    ContactPayload,
    ContactValidationError,
    is_spam,
    validate_contact_payload,
)


def _valid(**over):
    base = {"name": "Marie Curie", "email": "marie@chargeur.fr", "consent": True}
    base.update(over)
    return validate_contact_payload(**base)


def test_valid_payload_returns_normalized_object() -> None:
    p = _valid(company="  Chargeur SA  ", pol=" Fécamp ", message="  Bonjour  ")
    assert isinstance(p, ContactPayload)
    assert p.name == "Marie Curie"
    assert p.email == "marie@chargeur.fr"
    assert p.company == "Chargeur SA"  # trimmed
    assert p.pol == "Fécamp"
    assert p.message == "Bonjour"


def test_empty_optional_fields_become_none() -> None:
    p = _valid(company="   ", phone="")
    assert p.company is None
    assert p.phone is None


@pytest.mark.parametrize("field", ["name", "email"])
def test_required_fields_missing_raise(field: str) -> None:
    kwargs = {field: "  "}
    with pytest.raises(ContactValidationError) as exc:
        _valid(**kwargs)
    assert field in exc.value.errors


def test_consent_required() -> None:
    with pytest.raises(ContactValidationError) as exc:
        _valid(consent=False)
    assert exc.value.errors.get("consent") == "required"


@pytest.mark.parametrize("bad", ["nope", "a@b", "a@b.", "@domain.com", "x y@z.fr"])
def test_invalid_email_rejected(bad: str) -> None:
    with pytest.raises(ContactValidationError) as exc:
        _valid(email=bad)
    assert exc.value.errors.get("email") == "invalid"


@pytest.mark.parametrize("ok", ["a@b.co", "marie.curie@charge-ur.fr", "x+tag@sub.domain.io"])
def test_valid_emails_accepted(ok: str) -> None:
    assert _valid(email=ok).email == ok


def test_multiple_errors_collected() -> None:
    with pytest.raises(ContactValidationError) as exc:
        validate_contact_payload(name="", email="bad", consent=False)
    assert set(exc.value.errors) == {"name", "email", "consent"}


def test_long_field_is_capped() -> None:
    p = _valid(name="N" * 500)
    assert len(p.name) == 160  # _MAX["name"]


def test_lang_is_carried() -> None:
    assert _valid(lang="pt-br").lang == "pt-br"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("http://spam", True),
        ("x", True),
    ],
)
def test_is_spam_honeypot(value, expected) -> None:
    assert is_spam(value) is expected
