"""In-memory lab state for the Lab Agent."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class DeviceState:
    name: str
    mgmt_ip: str
    ready: bool = False
    last_check: float = 0.0


@dataclass
class LabState:
    topology_path: str = ""
    started: bool = False
    started_at: float = 0.0
    devices: dict[str, DeviceState] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "topology_path": self.topology_path,
            "started": self.started,
            "started_at": self.started_at,
            "devices": [
                {
                    "name": d.name,
                    "mgmt_ip": d.mgmt_ip,
                    "ready": d.ready,
                    "last_check": d.last_check,
                }
                for d in self.devices.values()
            ],
        }


class StateStore:
    """Single-lab in-memory state — one lab per golden image, by design."""

    def __init__(self) -> None:
        self._state = LabState()
        self._lock = asyncio.Lock()

    async def reset(self, *, topology_path: str, devices: dict[str, str]) -> None:
        """devices: mapping of device name -> mgmt_ip, parsed from the topology YAML."""
        async with self._lock:
            self._state = LabState(
                topology_path=topology_path,
                started=True,
                started_at=time.time(),
                devices={name: DeviceState(name=name, mgmt_ip=ip) for name, ip in devices.items()},
            )

    async def devices_snapshot(self) -> list[DeviceState]:
        async with self._lock:
            return list(self._state.devices.values())

    async def mark_ready(self, name: str, ready: bool) -> None:
        async with self._lock:
            if name in self._state.devices:
                self._state.devices[name].ready = ready
                self._state.devices[name].last_check = time.time()

    async def stop(self) -> None:
        async with self._lock:
            self._state.started = False
            self._state.devices = {}

    async def snapshot(self) -> dict:
        async with self._lock:
            return self._state.to_dict()


STORE = StateStore()
