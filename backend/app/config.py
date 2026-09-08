"""Application configuration via environment variables."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # General
    app_name: str = Field(default="lab-platform-backend")
    log_level: str = Field(default="INFO")

    # Database
    database_url: str = Field(default="postgresql+asyncpg://lab:lab@localhost:5432/labplatform")
    database_url_sync: str = Field(default="postgresql://lab:lab@localhost:5432/labplatform")

    # IPAM
    ipam_pool: str = Field(default="172.30.0.0/16")
    ipam_prefix_len: int = Field(default=24)

    # Resource quotas
    max_labs_per_host: int = Field(default=10)
    cpu_quota: int = Field(default=64)
    mem_quota_mb: int = Field(default=131072)

    # External services
    # The standalone ipam-service runs on port 8100 inside the docker network.
    # When run via docker-compose this resolves to the service name. For local
    # development (running backend outside docker), point at http://localhost:8100.
    ipam_service_url: str = Field(default="http://ipam-service:8100")

    # Hypervisor
    hypervisor_backend: Literal["mock", "local_ch"] = Field(default="mock")
    ch_binary: str = Field(default="/usr/local/bin/cloud-hypervisor")
    ch_kernel: str = Field(default="/var/lib/cloud-hypervisor/vmlinux")
    ch_image_dir: str = Field(default="/var/lib/cloud-hypervisor/images")
    ch_bridge: str = Field(default="br0")
    ch_tap_prefix: str = Field(default="tap")
    ch_disk_dir: str = Field(default="./var/runtime-disks")
    ch_api_socket_dir: str = Field(default="./var/ch-sockets")
    ch_seed_dir: str = Field(default="")
    ch_seed_file: str = Field(default="seed.iso")

    # Lab Agent
    lab_agent_base_url: str = Field(default="http://127.0.0.1:9001")
    device_readiness_timeout_sec: int = Field(default=1800)
    device_readiness_poll_sec: int = Field(default=5)

    # Backend server
    backend_host: str = Field(default="0.0.0.0")
    backend_port: int = Field(default=8000)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
