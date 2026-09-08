"""End-to-end test of the orchestrator using the mock hypervisor + mock Lab Agent client.

We monkey-patch `wf._lab_agent` to an in-memory fake so the test doesn't need the Lab Agent
running. The IPAM HTTP client is also monkey-patched so no real ipam-service is required.
"""
from __future__ import annotations

import asyncio
import ipaddress
import time

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Lab
from app.orchestrator import workflow as wf
from app.state_machine import LabStatus


class _FakeLabAgent:
    """Fake Lab Agent that reports devices as ready after `devices_ready_in` seconds.

    The fake's `status()` always reports `started=True` so the orchestrator's
    `_wait_for_devices_ready()` loop keeps polling until devices flip to ready.
    The "boot delay" timer starts when the fake is instantiated (effectively
    when the test sets things up), so a 0.2s `devices_ready_in` is enough.
    """

    def __init__(self, *, devices_ready_in: float = 0.2):
        self._created_at = time.time()
        self._devices_ready_in = devices_ready_in
        self.start_called = False
        self.stop_called = False

    async def health(self) -> bool:
        return True

    async def status(self) -> dict:
        boot = (time.time() - self._created_at) >= self._devices_ready_in
        devices = [
            {"name": "r1", "ready": boot},
            {"name": "r2", "ready": boot},
        ]
        return {"started": True, "devices": devices}

    async def start(self, *, lab_type: str, topology_path: str) -> dict:
        self.start_called = True
        return {"ok": True}

    async def stop(self) -> dict:
        self.stop_called = True
        return {"ok": True}

    async def console_url(self, device: str) -> str:
        return f"ws://stub/console/{device}"


class _FakeIPAMClient:
    """In-memory stand-in for the IPAM HTTP client.

    Mirrors the `IPAMClient` interface used by `workflow.py`:
        - allocate(lab_id) -> AllocationResult(subnet, gateway, vm_ip)
        - release(lab_id) -> None
    Hands out sequential /24 blocks; idempotent release.
    """

    def __init__(self, *, pool: str = "172.30.0.0/24", prefix_len: int = 28) -> None:
        self._net = ipaddress.ip_network(pool, strict=False)
        self._prefix_len = prefix_len
        self._blocks = [str(b) for b in self._net.subnets(new_prefix=prefix_len)]
        self._taken: set[str] = set()
        self._released: set[str] = set()

    async def allocate(self, lab_id: str):
        from app.ipam_client.client import AllocationResult

        for subnet in self._blocks:
            if subnet in self._taken or subnet in self._released:
                continue
            self._taken.add(subnet)
            net = ipaddress.ip_network(subnet, strict=False)
            return AllocationResult(
                subnet=subnet,
                gateway=str(net.network_address + 1),
                vm_ip=str(net.network_address + 10),
            )
        raise RuntimeError("IPAM exhausted in test fake")

    async def release(self, lab_id: str) -> None:
        self._released.update(self._taken)
        self._taken.clear()


@pytest.fixture
def fake_ipam(monkeypatch):
    fake = _FakeIPAMClient()
    monkeypatch.setattr(wf, "_ipam_client", fake)
    return fake


@pytest.mark.asyncio
async def test_full_lifecycle_to_lab_ready(monkeypatch, fake_ipam):
    fake = _FakeLabAgent(devices_ready_in=0.2)
    monkeypatch.setattr(wf, "_lab_agent", fake)
    # also patch the lab_agent_client used by api/health AND the per-lab
    # client built inside _wait_for_devices_ready() (which constructs
    # `LabAgentClient(f"http://{vm_ip}:9001", ...)` directly, bypassing
    # the module-level singleton). Patching at the `wf` module level is
    # what matters — workflow.py already did
    # `from app.lab_agent_client.client import LabAgentClient`, so it sees
    # `wf.LabAgentClient` as a name in its own namespace.
    from app.api import health as api_health

    monkeypatch.setattr(api_health, "LabAgentClient", lambda: fake)
    monkeypatch.setattr(wf, "LabAgentClient", lambda *a, **kw: fake)

    lab_id = "lab-e2e01"
    async with SessionLocal() as db:
        db.add(
            Lab(
                id=lab_id,
                lab_type="router",
                golden_image="router.qcow2",
                status=LabStatus.REQUESTED.value,
                cpu=2,
                memory_mb=2048,
            )
        )
        await db.commit()

    await wf.run_lab(lab_id, lab_type="router", cpu=2, memory_mb=2048)

    from sqlalchemy.orm import selectinload

    async with SessionLocal() as db:
        lab = (
            await db.execute(select(Lab).where(Lab.id == lab_id).options(selectinload(Lab.events)))
        ).scalar_one()
        events = list(lab.events)
        statuses = [e.to_status for e in sorted(events, key=lambda e: e.id)]
    assert lab.status == LabStatus.LAB_READY.value
    assert "NETWORK_ALLOCATED" in statuses
    assert "VM_READY" in statuses
    assert "LAB_READY" in statuses


@pytest.mark.asyncio
async def test_destroy_cleans_up(monkeypatch, fake_ipam):
    fake = _FakeLabAgent()
    monkeypatch.setattr(wf, "_lab_agent", fake)

    lab_id = "lab-e2e02"
    async with SessionLocal() as db:
        db.add(
            Lab(
                id=lab_id,
                lab_type="router",
                golden_image="router.qcow2",
                status=LabStatus.LAB_READY.value,
                cpu=2,
                memory_mb=2048,
                subnet="172.30.5.0/28",
                gateway="172.30.5.1",
                vm_ip="172.30.5.10",
                tap_name="tap-lab-e2e02",
                vm_pid=99999,
            )
        )
        await db.commit()

    await wf.destroy_lab(lab_id)

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
    assert lab.status == LabStatus.DESTROYED.value
    assert fake.stop_called


@pytest.mark.asyncio
async def test_device_boot_timeout_marks_failed(monkeypatch, fake_ipam):
    """If the Lab Agent reports devices not ready, the lab must end in FAILED."""

    class _NeverReady(_FakeLabAgent):
        async def status(self) -> dict:
            return {"started": True, "devices": [{"name": "r1", "ready": False}]}

    fake = _NeverReady()
    monkeypatch.setattr(wf, "_lab_agent", fake)

    lab_id = "lab-e2e03"
    async with SessionLocal() as db:
        db.add(
            Lab(
                id=lab_id,
                lab_type="router",
                golden_image="router.qcow2",
                status=LabStatus.REQUESTED.value,
                cpu=2,
                memory_mb=2048,
            )
        )
        await db.commit()

    # run_lab catches the timeout exception internally and marks FAILED + cleans up.
    await wf.run_lab(lab_id, lab_type="router", cpu=2, memory_mb=2048)

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
    assert lab.status in {LabStatus.FAILED.value, LabStatus.DESTROYED.value}
    assert lab.error and "HEALTH_CHECK_TIMEOUT" in lab.error