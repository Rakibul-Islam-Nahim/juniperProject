"""Lab Agent configuration."""
from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")
    agent_host: str = "0.0.0.0"
    agent_port: int = 9001
    log_level: str = "INFO"
    # Fixed topology for this golden image. Every image has exactly one lab
    # baked in, so this is set via a per-image .env, never passed at runtime.
    topology_path: str = "/opt/lab_agent/topologies/router.clab.yml"
    # TCP port used to probe each device's management plane for readiness.
    # Junos exposes both ssh (22) and netconf-over-ssh (830); 22 is checked
    # by default since it's the more universal "management plane is up" signal.
    readiness_check_port: int = 22
    readiness_poll_interval_sec: float = 2.0
    readiness_connect_timeout_sec: float = 2.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
