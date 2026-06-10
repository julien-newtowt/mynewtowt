"""Tests for app.services.packing_list — token hashing & access control."""

from __future__ import annotations

from app.models.packing_list import PackingList
from app.services.packing_list import can_modify, hash_token


def test_hash_token_is_deterministic():
    h1 = hash_token("abcdef")
    h2 = hash_token("abcdef")
    assert h1 == h2


def test_hash_token_changes_for_different_inputs():
    assert hash_token("abc") != hash_token("xyz")


def test_hash_token_is_sha256_length():
    h = hash_token("token24chars_aabbccddeeff")
    # sha256 = 64 hex chars
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_can_modify_draft():
    pl = PackingList(order_id=1, token="abc", status="draft")
    assert can_modify(pl) is True


def test_can_modify_submitted():
    pl = PackingList(order_id=1, token="abc", status="submitted")
    assert can_modify(pl) is True


def test_can_modify_locked_is_false():
    pl = PackingList(order_id=1, token="abc", status="locked")
    assert can_modify(pl) is False
