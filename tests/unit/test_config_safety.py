"""Config / startup-safety tests.

Make sure prod refuses to start with weak DB password but accepts a strong
one — even though the URL scheme starts with `postgresql+asyncpg://`,
which contains the substring "postgres".
"""
from __future__ import annotations

import pytest

from app.config import WEAK_DB_PASSWORDS, Settings


def _build(**env_overrides: str) -> Settings:
    """Build a Settings without going through the env / .env file."""
    base = {
        "secret_key": "test_only_secret_key_with_more_than_32_characters_xxx",
        "database_url": "postgresql+asyncpg://towt:strong-random-pass@db:5432/towt",
        "app_env": "production",
    }
    base.update(env_overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_prod_rejects_weak_password() -> None:
    s = _build(database_url="postgresql+asyncpg://towt:postgres@db:5432/towt")
    with pytest.raises(RuntimeError, match="weak list"):
        s._enforce_prod_safety()


@pytest.mark.parametrize("weak", sorted(WEAK_DB_PASSWORDS))
def test_prod_rejects_all_weak_passwords(weak: str) -> None:
    s = _build(database_url=f"postgresql+asyncpg://towt:{weak}@db:5432/towt")
    with pytest.raises(RuntimeError, match="weak list"):
        s._enforce_prod_safety()


def test_prod_accepts_strong_password_with_postgres_in_scheme() -> None:
    """Regression: 'postgres' in the scheme must not trigger the gate."""
    s = _build(database_url="postgresql+asyncpg://towt:Ax9-Q2zVm7-randomized@db:5432/towt")
    s._enforce_prod_safety()  # must not raise


def test_prod_rejects_stripe_test_key() -> None:
    s = _build(stripe_secret_key="sk_test_abcdef")
    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY is a test key"):
        s._enforce_prod_safety()


def test_prod_accepts_stripe_live_key() -> None:
    s = _build(stripe_secret_key="sk_live_realkeyhere")
    s._enforce_prod_safety()  # must not raise


def test_dev_skips_all_checks() -> None:
    s = _build(
        app_env="development",
        database_url="postgresql+asyncpg://towt:postgres@db:5432/towt",
        stripe_secret_key="sk_test_abcdef",
    )
    s._enforce_prod_safety()  # must not raise


def test_weak_secret_key_rejected() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError
        Settings(  # type: ignore[call-arg]
            secret_key="secret",
            database_url="postgresql+asyncpg://towt:strong@db:5432/towt",
        )


def test_short_secret_key_rejected() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError
        Settings(  # type: ignore[call-arg]
            secret_key="too_short",
            database_url="postgresql+asyncpg://towt:strong@db:5432/towt",
        )
