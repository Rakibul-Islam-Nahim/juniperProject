"""Hypervisor backend abstraction."""
from __future__ import annotations

import abc
from dataclasses import dataclass


class VMStartError(RuntimeError):
    pass


class VMNotReady(RuntimeError):
    pass


@dataclass
class VMHandle:
    """Opaque reference to a running VM."""

    pid: int
    api_socket: str | None
    console_socket: str | None
    tap: str
    vm_ip: str | None = None


class HypervisorBackend(abc.ABC):
    @abc.abstractmethod
    async def start(
        self,
        *,
        lab_id: str,
        image: str,
        cpu: int,
        memory_mb: int,
        tap_name: str,
        vm_ip: str,
        gateway: str,
        subnet: str,
    ) -> VMHandle: ...

    @abc.abstractmethod
    async def is_running(self, handle: VMHandle) -> bool: ...

    @abc.abstractmethod
    async def wait_ready(self, handle: VMHandle, *, timeout_sec: float) -> None: ...

    @abc.abstractmethod
    async def stop(self, handle: VMHandle) -> None: ...
