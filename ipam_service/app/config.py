"""IPAM Service configuration."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "lab-ipam-service"
    log_level: str = "INFO"

    # Database — IPAM service exclusively owns the ip_allocations table.
    # Default points at the docker-compose service name; override with DATABASE_URL.
    database_url: str = "postgresql+asyncpg://lab:lab@postgres:5432/labplatform"
    database_url_sync: str = "postgresql://lab:lab@postgres:5432/labplatform"

    # IPAM pool
    ipam_pool: str = "172.30.0.0/16"
    ipam_prefix_len: int = 24

    # Server
    service_host: str = "0.0.0.0"
    service_port: int = 8100


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()