"""Pytest fixtures shared across the test suite."""
from __future__ import annotations

import os

# Provide safe defaults so importing app/config doesn't fail in CI.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault(
    "SECRET_KEY", "test_only_secret_key_with_more_than_32_characters_xxx"
)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://towt:change_me_local@localhost:5432/towt_test",
)
