"""Cookie kwargs — Secure flag must reflect the effective scheme.

The bug we're guarding against: setting Secure=True over plain HTTP
makes browsers silently drop the cookie, breaking sessions.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.auth import cookie_kwargs_for_client, cookie_kwargs_for_staff


def _fake_request(scheme: str, forwarded_proto: str | None = None) -> Any:
    return SimpleNamespace(
        url=SimpleNamespace(scheme=scheme),
        headers={"x-forwarded-proto": forwarded_proto} if forwarded_proto else {},
    )


def test_http_request_yields_secure_false() -> None:
    for kwargs in (
        cookie_kwargs_for_staff(_fake_request("http")),
        cookie_kwargs_for_client(_fake_request("http")),
    ):
        assert kwargs["secure"] is False


def test_https_request_yields_secure_true() -> None:
    for kwargs in (
        cookie_kwargs_for_staff(_fake_request("https")),
        cookie_kwargs_for_client(_fake_request("https")),
    ):
        assert kwargs["secure"] is True


def test_forwarded_proto_https_yields_secure_true() -> None:
    """nginx terminates TLS; the app sees http but X-Forwarded-Proto=https."""
    for kwargs in (
        cookie_kwargs_for_staff(_fake_request("http", forwarded_proto="https")),
        cookie_kwargs_for_client(_fake_request("http", forwarded_proto="https")),
    ):
        assert kwargs["secure"] is True


def test_forwarded_proto_http_overrides_https_scheme() -> None:
    """If a (mis)configured proxy forwards http, trust the forwarded header."""
    kwargs = cookie_kwargs_for_staff(
        _fake_request("https", forwarded_proto="http")
    )
    assert kwargs["secure"] is False


def test_no_request_defaults_to_secure_false() -> None:
    """Bootstrap-friendly default: no request → no Secure flag."""
    for kwargs in (cookie_kwargs_for_staff(None), cookie_kwargs_for_client(None)):
        assert kwargs["secure"] is False


def test_other_kwargs_constants() -> None:
    kwargs = cookie_kwargs_for_staff(_fake_request("https"))
    assert kwargs["httponly"] is True
    assert kwargs["samesite"] == "lax"
    assert kwargs["path"] == "/"
    assert kwargs["key"] == "towt_session"
