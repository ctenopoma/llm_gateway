"""
Application configuration using pydantic-settings.
All values are read from environment variables or .env file.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache

# System admin user OID — used for audit logging from the admin panel.
# This user must exist in the Users table and must not be deleted.
SYSTEM_ADMIN_OID = "SYSTEM_ADMIN"


class Settings(BaseSettings):
    """Gateway configuration."""

    # Database
    DATABASE_URL: str = "postgresql://gateway:gateway@localhost:5432/llm_gateway"
    DB_POOL_MIN_SIZE: int = 5
    DB_POOL_MAX_SIZE: int = 20

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Gateway Authentication
    GATEWAY_SHARED_SECRET: str = "change-me"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "info"
    WORKER_COUNT: int = 1

    # API Key Cache
    API_KEY_CACHE_TTL: int = 60  # seconds

    # Budget
    BUDGET_RESERVATION_TTL: int = 300  # seconds
    BUDGET_DB_CACHE_TTL: int = 5  # seconds

    # Health Check
    HEALTH_CHECK_POLL_INTERVAL: int = 5  # seconds
    HEALTH_CHECK_BATCH_SIZE: int = 50

    # Admin Panel
    ADMIN_PASSWORD: str = "admin"
    ADMIN_JWT_SECRET: str = "change-me-admin-jwt"
    ADMIN_SESSION_HOURS: int = 24

    # Log Retention
    LOG_RETENTION_DAYS: int = 90  # 0 = never delete

    model_config = {"env_file": ".env", "case_sensitive": True}


@lru_cache
def get_settings() -> Settings:
    return Settings()
