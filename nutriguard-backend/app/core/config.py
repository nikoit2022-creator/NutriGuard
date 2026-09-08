"""
Central application configuration.

All values are loaded from environment variables (see `.env.example`).
Never hard-code secrets here.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- General ---
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "NutriGuard Backend"
    APP_VERSION: str = "1.0.0"

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://nutriguard:nutriguard@localhost:5432/nutriguard"

    # --- Redis (cache / rate limiting) ---
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = True

    # --- Auth / JWT ---
    JWT_SECRET: str = "CHANGE_ME_IN_PRODUCTION"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_SECONDS: int = 3600
    REFRESH_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 24 * 30  # 30 days

    # --- CORS ---
    CORS_ORIGINS: str = "*"

    # --- Gemini integration ---
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash"
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_TIMEOUT_SECONDS: float = 60.0

    # --- Uploads ---
    MAX_IMAGE_SIZE_BYTES: int = 8 * 1024 * 1024  # 8 MB, matches client JPEG q80 compression

    # --- Temporary, bounded scan diagnostics ---
    SCAN_DIAGNOSTICS_ENABLED: bool = False
    SCAN_DIAGNOSTICS_PATH: str = "/var/log/nutriguard/scan-diagnostics.jsonl"
    SCAN_DIAGNOSTICS_MAX_BYTES: int = 1024 * 1024
    SCAN_DIAGNOSTICS_BACKUP_COUNT: int = 1

    # --- Rate limiting (requests per window, per identity) ---
    RATE_LIMIT_SCAN_PER_HOUR: int = 30
    RATE_LIMIT_READ_PER_HOUR: int = 300
    RATE_LIMIT_PROFILE_PER_HOUR: int = 60
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10

    # --- Barcode product discovery (multi-source lookup on a local miss) ---
    # Master switch: when false, an unknown barcode goes straight to the
    # structured "not found / label scan required" response with zero
    # external calls, regardless of the per-provider flags below.
    BARCODE_DISCOVERY_ENABLED: bool = True

    OPEN_FOOD_FACTS_ENABLED: bool = True
    OPEN_FOOD_FACTS_BASE_URL: str = "https://world.openfoodfacts.org"
    OPEN_FOOD_FACTS_USER_AGENT: str = "NutriGuard-Backend/1.0 (+https://github.com/nikoit2022-creator/NutriGuard)"

    GS1_RESOLVER_ENABLED: bool = True
    # GS1's own global Digital Link resolver. Treated strictly as a
    # resolver for an official manufacturer/GS1-registered resource, not
    # as a product database — see app/integrations/barcode_providers/gs1_resolver.py.
    GS1_RESOLVER_BASE_URL: str = "https://id.gs1.org"

    UPCITEMDB_ENABLED: bool = True
    # Free "trial" endpoint by default: works with no credentials (rate
    # limited). A paid UPCitemdb plan can be used by setting
    # UPCITEMDB_BASE_URL to the prod/v1 host and providing the keys below.
    UPCITEMDB_BASE_URL: str = "https://api.upcitemdb.com/prod/trial"
    UPCITEMDB_API_KEY: str = ""
    UPCITEMDB_USER_KEY: str = ""

    # Per-request connect/read timeout for any barcode provider call.
    BARCODE_PROVIDER_TIMEOUT_SECONDS: float = 5.0
    # Extra attempts (beyond the first) on a transient failure (timeout /
    # network error / 5xx) for a single provider. Never retries on a 4xx,
    # a rate-limit response, or a malformed body — those move on to the
    # next provider immediately instead.
    BARCODE_PROVIDER_MAX_RETRIES: int = 1
    # How long a previously discovered (externally sourced, unverified)
    # product is considered fresh before a later scan is eligible to
    # re-verify/refresh it from providers again.
    BARCODE_DISCOVERY_CACHE_SECONDS: int = 30 * 24 * 60 * 60  # 30 days

    # --- Ingredient catalog cache (persistent ingredient knowledge cache) ---
    # How long a VERIFIED ingredient's regulatory/scientific data is
    # trusted before it's flagged for revalidation (see
    # app.services.ingredient_catalog.is_stale). Never blocks a scan --
    # the last known value is still served immediately, only marked
    # `needsRefresh` once expired.
    INGREDIENT_VERIFIED_DATA_TTL_SECONDS: int = 180 * 24 * 60 * 60  # ~6 months
    # How long a not-yet-VERIFIED (UNVERIFIED/LIMITED_DATA) ingredient
    # record is treated as a fresh negative-cache entry, so the same
    # unresolved ingredient isn't re-attempted for every product within
    # this window (see app.services.ingredient_catalog.is_within_negative_cache_window).
    INGREDIENT_NEGATIVE_CACHE_TTL_SECONDS: int = 24 * 60 * 60  # 24 hours

    @property
    def cors_origins_list(self) -> List[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
