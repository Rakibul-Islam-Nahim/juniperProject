"""TAP / bridge management.

The real implementation shells out to `ip` to create TAP interfaces and attach them to a
bridge. In mock mode (no CH host available) these calls are no-ops that just log.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from app.config import get_settings
from app.logging import get_logger

_log = get_logger("networking")


class NetworkingError(RuntimeError):
    pass


@dataclass
class TapInfo:
    name: str
    bridge: str


class NetworkingService:
    """Real implementation uses `ip` commands. Mock mode is a no-op."""

    def __init__(self, bridge: str, tap_prefix: str, mock: bool) -> None:
        self.bridge = bridge
        self.tap_prefix = tap_prefix
        self.mock = mock

    @classmethod
    def from_settings(cls) -> "NetworkingService":
        s = get_settings()
        return cls(
            bridge=s.ch_bridge,
            tap_prefix=s.ch_tap_prefix,
            mock=s.hypervisor_backend == "mock",
        )

    @staticmethod
    def has_ip_command() -> bool:
        return shutil.which("ip") is not None

    async def create_tap_and_attach(self, tap_name: str) -> TapInfo:
        info = TapInfo(name=tap_name, bridge=self.bridge)
        if self.mock or not self.has_ip_command():
            _log.info("tap.create (mock/no-ip)", tap=tap_name, bridge=self.bridge)
            return info

        await self._run(["ip", "tuntap", "add", "dev", tap_name, "mode", "tap"])
        await self._run(["ip", "link", "set", "dev", tap_name, "up"])
        await self._run(["ip", "link", "set", "dev", tap_name, "master", self.bridge])
        _log.info("tap.create", tap=tap_name, bridge=self.bridge)
        return info

    async def remove_tap(self, tap_name: str) -> None:
        if self.mock or not self.has_ip_command():
            _log.info("tap.remove (mock/no-ip)", tap=tap_name)
            return
        await self._run(["ip", "link", "set", "dev", tap_name, "down"], ignore_errors=True)
        await self._run(["ip", "link", "delete", tap_name], ignore_errors=True)
        _log.info("tap.remove", tap=tap_name)

    async def _run(self, cmd: list[str], *, ignore_errors: bool = False) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError as e:
            if ignore_errors:
                _log.warning("networking.cmd.missing", cmd=cmd, error=str(e))
                return
            raise NetworkingError(f"command not found: {cmd[0]}") from e

        if proc.returncode != 0 and not ignore_errors:
            raise NetworkingError(
                f"{' '.join(cmd)} failed: {stderr.decode().strip() or stdout.decode().strip()}"
            )