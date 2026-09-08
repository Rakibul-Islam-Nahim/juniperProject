"""HTTP client for the standalone IPAM service.

Mirrors the in-process `IPAMService` API (`allocate`, `hard_release`) but goes
over HTTP. The Backend Agent calls into this instead of touching the
`ip_allocations` table directly.

Failure model (per user direction — no retries, fail loud):
  - Network errors / 5xx        -> `IPAMUnavailable` (caller logs and bails).
  - 503 with code IPAM_EXHAUSTED -> `IPAMExhausted` (pool full — caller marks lab FAILED).
  - Other HTTP errors            -> `IPAMError`.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.logging import get_logger

_log = get_logger("ipam_client")


class IPAMError(RuntimeError):
    pass


class IPAMExhausted(IPAMError):
    pass


class IPAMUnavailable(IPAMError):
    pass


@dataclass(frozen=True)
class AllocationResult:
    subnet: str
    gateway: str
    vm_ip: str


class IPAMClient:
    def __init__(self, base_url: str, *, timeout_sec: float = 30.0) -> None:
        # Strip trailing slash so we can build URLs with f"{base}/allocate".
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec

    @classmethod
    def from_settings(cls) -> "IPAMClient":
        s = get_settings()
        return cls(s.ipam_service_url, timeout_sec=30.0)

    async def allocate(self, lab_id: str) -> AllocationResult:
        """Allocate a /prefix_len block to `lab_id`.

        Raises:
            IPAMExhausted: pool is full (HTTP 503 IPAM_EXHAUSTED).
            IPAMUnavailable: network / 5xx error.
            IPAMError: other failure.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as c:
                resp = await c.post(f"{self._base_url}/allocate", json={"lab_id": lab_id})
        except httpx.HTTPError as e:
            raise IPAMUnavailable(f"ipam-service unreachable: {e}") from e

        if resp.status_code == 200:
            body = resp.json()
            return AllocationResult(
                subnet=body["subnet"],
                gateway=body["gateway"],
                vm_ip=body["vm_ip"],
            )

        # Parse the structured error envelope.
        try:
            err = resp.json().get("error", {})
            code = err.get("code", "UNKNOWN")
            msg = err.get("message", resp.text)
        except (ValueError, AttributeError):
            code, msg = "UNKNOWN", resp.text

        if resp.status_code == 503 and code == "IPAM_EXHAUSTED":
            raise IPAMExhausted(msg)
        if resp.status_code >= 500:
            raise IPAMUnavailable(f"ipam-service {resp.status_code}: {msg}")
        raise IPAMError(f"ipam-service {resp.status_code} ({code}): {msg}")

    async def release(self, lab_id: str) -> None:
        """Idempotent hard-release of `lab_id`'s allocation.

        Raises:
            IPAMUnavailable: network / 5xx error.
            IPAMError: other failure.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as c:
                resp = await c.post(f"{self._base_url}/release/{lab_id}")
        except httpx.HTTPError as e:
            raise IPAMUnavailable(f"ipam-service unreachable during release: {e}") from e

        if resp.status_code == 200:
            return

        try:
            err = resp.json().get("error", {})
            msg = err.get("message", resp.text)
        except (ValueError, AttributeError):
            msg = resp.text

        if resp.status_code >= 500:
            raise IPAMUnavailable(f"ipam-service release {resp.status_code}: {msg}")
        raise IPAMError(f"ipam-service release failed {resp.status_code}: {msg}")