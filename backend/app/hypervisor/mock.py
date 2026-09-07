"""In-memory mock hypervisor.

Simulates the VM lifecycle on a background asyncio task. Used on dev workstations where
no Cloud Hypervisor host is available.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

from app.hypervisor.base import HypervisorBackend, VMHandle, VMNotReady, VMStartError
from app.logging import get_logger

_log = get_logger("hypervisor.mock")


@dataclass
class _MockState:
    handle: VMHandle
    created_at: float
    running: bool = True
    ready: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


class MockHypervisorBackend(HypervisorBackend):
    def __init__(self, *, boot_delay_sec: float = 1.0) -> None:
        self._boot_delay_sec = boot_delay_sec
        self._states: dict[int, _MockState] = {}
        self._lock = asyncio.Lock()

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
    ) -> VMHandle:
        async with self._lock:
            pid = os.getpid() * 1000 + len(self._states) + 1
            api_socket = f"/tmp/labplatform-{lab_id}.ch.sock"
            console_socket = f"/tmp/labplatform-{lab_id}.console.sock"
            handle = VMHandle(pid=pid, api_socket=api_socket, console_socket=console_socket, tap=tap_name)
            state = _MockState(handle=handle, created_at=time.time())
            self._states[handle.pid] = state
            _log.info(
                "vm.start (mock)",
                lab_id=lab_id,
                pid=pid,
                image=image,
                cpu=cpu,
                memory_mb=memory_mb,
                tap=tap_name,
                vm_ip=vm_ip,
                gateway=gateway,
                subnet=subnet,
            )

            async def _boot() -> None:
                try:
                    await asyncio.sleep(self._boot_delay_sec)
                    if state.stop_event.is_set():
                        return
                    state.ready = True
                    _log.info("vm.ready (mock)", lab_id=lab_id, pid=pid)
                except asyncio.CancelledError:
                    return

            asyncio.create_task(_boot(), name=f"mock-vm-{lab_id}")
            return handle

    async def is_running(self, handle: VMHandle) -> bool:
        state = self._states.get(handle.pid)
        return state is not None and state.running and not state.stop_event.is_set()

    async def wait_ready(self, handle: VMHandle, *, timeout_sec: float) -> None:
        state = self._states.get(handle.pid)
        if state is None:
            raise VMNotReady(f"unknown vm handle pid={handle.pid}")

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if state.stop_event.is_set():
                raise VMStartError(f"vm pid={handle.pid} was stopped before becoming ready")
            if state.ready:
                return
            await asyncio.sleep(0.05)
        raise VMNotReady(f"vm pid={handle.pid} did not become ready within {timeout_sec}s")

    async def stop(self, handle: VMHandle) -> None:
        state = self._states.get(handle.pid)
        if state is None:
            return
        state.stop_event.set()
        state.running = False
        state.ready = False
        _log.info("vm.stop (mock)", pid=handle.pid)
        # do not delete immediately — keep the row for inspection
