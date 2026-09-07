"""HTTP client for the in-microVM Lab Agent."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import get_settings
from app.logging import get_logger

_log = get_logger("lab_agent_client")


class LabAgentError(RuntimeError):
    pass


class LabAgentClient:
    def __init__(self, base_url: str, *, timeout_sec: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec

    @classmethod
    def from_settings(cls) -> "LabAgentClient":
        s = get_settings()
        return cls(s.lab_agent_base_url, timeout_sec=float(s.device_readiness_timeout_sec))

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{self._base_url}/health")
                return r.status_code == 200
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            _log.warning("lab_agent.health.error", error=str(e))
            return False

    async def status(self) -> dict[str, Any]:
        return await self._get("/status")

    async def start(self, *, lab_type: str, topology_path: str) -> dict[str, Any]:
        return await self._post("/start", {"lab_type": lab_type, "topology_path": topology_path})

    async def stop(self) -> dict[str, Any]:
        return await self._post("/stop", {})

    async def console_url(self, device: str) -> str:
        """Return the upstream ws:// URL to connect to for a device's console."""
        return f"{self._base_url.replace('http', 'ws', 1)}/console/{device}"

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as c:
                r = await c.get(f"{self._base_url}{path}")
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            raise LabAgentError(f"GET {path} failed: {e}") from e

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as c:
                r = await c.post(f"{self._base_url}{path}", json=body)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            raise LabAgentError(f"POST {path} failed: {e}") from e