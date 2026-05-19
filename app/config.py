"""Application settings — single source of truth for environment config.

Refuse to start in production with weak secrets or default DB credentials,
mirroring the safety policy established in V2 and reinforced for V3.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WEAK_SECRETS: set[str] = {
    "towt_secret_key_change_in_production_2025",
    "change_me",
    "changeme",
    "secret",
    "change_me_to_a_random_32_chars_or_more_string_here_please",
}

WEAK_DB_PASSWORDS: set[str] = {
    "towt_secure_2025",
    "change_me_local",
    "postgres",
    "password",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "mynewtowt"
    app_version: str = "3.0.0"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    site_url: str = "http://localhost:8000"

    # Security
    secret_key: str
    access_token_expire_minutes: int = 480  # staff 8h
    client_session_days: int = 30           # client persistent
    algorithm: str = "HS256"

    # Database
    database_url: str
    postgres_user: str = "towt"
    postgres_password: str = "change_me_local"
    postgres_db: str = "towt"

    # Initial admin
    initial_admin_username: str = "admin"
    initial_admin_email: str = "admin@newtowt.eu"
    initial_admin_password: str = "ChangeMeFirst!2026"

    # External
    pipedrive_api_token: str | None = None
    stripe_publishable_key: str | None = None
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    anthropic_api_key: str | None = None
    windy_api_key: str | None = None
    mapbox_token: str | None = None
    maptiler_token: str | None = None
    tracking_api_token: str | None = None

    @property
    def map_token(self) -> str:
        """Resolved token for MapLibre tiles. Prefers MAPTILER_TOKEN, falls
        back to MAPBOX_TOKEN for backward compatibility with earlier .env."""
        return self.maptiler_token or self.mapbox_token or ""

    # Email
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from_name: str = "NEWTOWT"
    smtp_from_address: str = "no-reply@newtowt.eu"

    # Observability
    sentry_dsn: str | None = None
    otel_exporter_otlp_endpoint: str | None = None
    prometheus_metrics: bool = True

    # Backup
    backup_retention_days: int = 7
    backup_s3_bucket: str | None = None
    backup_gpg_recipient: str | None = None

    domain: str = "my.newtowt.eu"
    certbot_email: str = "ops@newtowt.eu"

    @field_validator("secret_key")
    @classmethod
    def _secret_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        if v in WEAK_SECRETS:
            raise ValueError(
                "SECRET_KEY is in the weak secrets list — choose a real random value"
            )
        return v

    @field_validator("database_url")
    @classmethod
    def _db_url_safe(cls, v: str) -> str:
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            raise ValueError(
                "DATABASE_URL must use the async driver: postgresql+asyncpg://"
            )
        return v

    def _enforce_prod_safety(self) -> None:
        """Hard refusals if running in production with weak config."""
        if self.app_env != "production":
            return
        from urllib.parse import urlparse

        parsed = urlparse(self.database_url)
        password = parsed.password or ""
        if password in WEAK_DB_PASSWORDS:
            raise RuntimeError(
                f"Production refusing to start: DATABASE_URL password is in the "
                f"weak list ({password!r}). Generate a random one."
            )
        if self.stripe_secret_key and self.stripe_secret_key.startswith("sk_test_"):
            raise RuntimeError(
                "Production refusing to start: STRIPE_SECRET_KEY is a test key (sk_test_)."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def enforce_production_safety() -> None:
    """Call this at app startup. Raises RuntimeError on unsafe prod config."""
    get_settings()._enforce_prod_safety()


settings = get_settings()
