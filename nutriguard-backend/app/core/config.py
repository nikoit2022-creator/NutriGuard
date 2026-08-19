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

    # --- Rate limiting (requests per window, per identity) ---
    RATE_LIMIT_SCAN_PER_HOUR: int = 30
    RATE_LIMIT_READ_PER_HOUR: int = 300
    RATE_LIMIT_PROFILE_PER_HOUR: int = 60
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10

    @property
    def cors_origins_list(self) -> List[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
