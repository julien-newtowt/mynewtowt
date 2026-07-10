"""Application settings — single source of truth for environment config.

Refuse to start in production with weak secrets or default DB credentials,
mirroring the safety policy established in V2 and reinforced for V3.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
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

    # Stockage des pièces jointes uploadées (packing list docs, pièces client).
    # Relatif au cwd en dev ; mettre un chemin absolu en prod (volume monté).
    upload_dir: str = "var/uploads"

    # Security
    secret_key: str
    access_token_expire_minutes: int = 480  # staff 8h
    client_session_days: int = 30  # client persistent
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
    # Pipeline cible des deals créés depuis mytowt (leads web/devis). Résolu par
    # nom via l'API Pipedrive ; le premier étage du pipeline est utilisé.
    pipedrive_pipeline_name: str = "Deals from web"
    anthropic_api_key: str | None = None
    windy_api_key: str | None = None
    mapbox_token: str | None = None
    maptiler_token: str | None = None
    tracking_api_token: str | None = None
    # SEC-06 — clé d'API publique B2B (header X-API-Key) pour /api/v1/*. Sans
    # cette valeur, l'API v1 (read-only) renvoie 503 : secure-by-default, l'API
    # externe reste fermée tant qu'aucune clé n'est provisionnée.
    public_api_key: str | None = None
    # Token X-API-Token pour POST /api/tickets/escalate-sla (cron Power
    # Automate : escalade SLA des tickets escale dont le délai est dépassé).
    tickets_sla_api_token: str | None = None
    # Token X-API-Token pour POST /api/weather/refresh (cron Power Automate
    # toutes les 30 min : snapshot météo Windy du dernier point GPS de chaque
    # navire, historisé pour consultation ultérieure des legs réalisés).
    weather_api_token: str | None = None

    # Marad (MaraSoft « Generic API ») — ship & crew management. Intégration
    # LECTURE SEULE des données crew (cf. docs/integrations/marad-crew-readonly.md).
    # mynewtowt n'écrit jamais dans Marad. Sans MARAD_API_TOKEN, le client est
    # un no-op et l'endpoint cron renvoie 503.
    marad_base_url: str = "https://external.marad.ms"
    marad_api_token: str | None = None  # clé d'API Marad (envoyée en header)
    # Header d'auth. NON configuré (None) → le client sonde les headers usuels
    # (X-Api-Key, ApiKey, ApiToken, Authorization) puis la query string, et
    # mémorise celui qui marche. Configuré (n'importe quelle valeur, y compris
    # "X-Api-Key") → ce header est épinglé et essayé seul (un appel, pas de
    # cascade sur les endpoints à 1 req/min).
    marad_api_key_header: str | None = None
    marad_sync_token: str | None = None  # X-API-Token du cron interne POST /api/marad/refresh
    # Les 2 endpoints crew (/api/Crewing + /api/CrewingSchedule) sont à 1 req/min
    # et ne peuvent être appelés coup sur coup : le cron patiente ce délai (s)
    # puis retente les plannings UNE fois. 0 = pas de retry (le 429 est remonté).
    marad_schedule_retry_wait: float = 65.0
    # Repli de mapping navire. Marad identifie un navire par {number, name}
    # (/api/vessels/getVessels) ; la sync résout d'abord par nom/code de notre
    # table Vessel, puis via cette map "marad_number_ou_nom=vessel_id,...".
    marad_vessel_map: str = ""

    # Veille d'actualité — agrégateur NewsData.io + token de rafraîchissement
    # (POST /api/veille/refresh, déclenché en cron par Power Automate).
    newsdata_api_key: str | None = None
    newsdata_base_url: str = "https://newsdata.io/api/1/latest"
    veille_api_token: str | None = None

    # Relance J+1 sur devis non converti (nurturing avant-vente). Token du cron
    # externe (POST /api/quotes/followup, déclenché par Power Automate).
    quote_followup_api_token: str | None = None

    # Note V3.1 — Stripe retiré de la facturation FRET : NEWTOWT facture le
    # fret par virement bancaire (cf. pdf/invoice.html), l'équipe commerciale
    # confirme les bookings sous 4h.
    #
    # Réintroduit de façon CIBLÉE pour la « vente à bord » (encaissement CB des
    # collaborateurs embarqués) via Stripe Checkout + webhook. Secure-by-default :
    # sans STRIPE_SECRET_KEY, la génération de lien CB renvoie 503 (l'app tourne
    # normalement, seule la voie carte est indisponible ; l'encaissement espèces
    # reste actif). Le webhook exige STRIPE_WEBHOOK_SECRET pour vérifier la
    # signature ; sans lui, il renvoie 503.
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_publishable_key: str | None = None

    @property
    def stripe_enabled(self) -> bool:
        """Vrai si l'encaissement carte (Stripe Checkout) est configuré."""
        return bool(self.stripe_secret_key)

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
    # Boîte de l'équipe commerciale — reçoit les nouveaux leads (formulaire
    # de contact public). None → envoi silencieusement ignoré.
    commercial_inbox_email: str | None = None

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

    # Force MFA pour le rôle administrateur — middleware
    # ForceMfaForAdminMiddleware redirige vers /admin/my-account/mfa
    # tant que l'admin n'a pas activé MFA. À mettre False en dev local.
    require_mfa_for_admin: bool = True
    # ``site_url`` = origin attendu pour les attestations. En .env :
    # SITE_URL=https://my.newtowt.eu (sans trailing slash).

    @field_validator("secret_key")
    @classmethod
    def _secret_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        if v in WEAK_SECRETS:
            raise ValueError("SECRET_KEY is in the weak secrets list — choose a real random value")
        return v

    @field_validator("database_url")
    @classmethod
    def _db_url_safe(cls, v: str) -> str:
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            raise ValueError("DATABASE_URL must use the async driver: postgresql+asyncpg://")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def enforce_production_safety() -> None:
    """Call this at app startup. Raises RuntimeError on unsafe prod config."""
    get_settings()._enforce_prod_safety()


settings = get_settings()
