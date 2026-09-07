"""End-to-end test of the orchestrator using the mock hypervisor + mock Lab Agent client.

We monkey-patch `wf._lab_agent` to an in-memory fake so the test doesn't need the Lab Agent
running.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Lab
from app.orchestrator import workflow as wf
from app.state_machine import LabStatus


class _FakeLabAgent:
    def __init__(self, *, devices_ready_in: float = 0.2):
        self._ready_at = None
        self._devices_ready_in = devices_ready_in
        self.start_called = False
        self.stop_called = False

    async def health(self) -> bool:
        return True

    async def status(self) -> dict:
        boot = self._ready_at is not None and (time.time() - self._ready_at) >= self._devices_ready_in
        devices = [
            {"name": "r1", "ready": boot},
            {"name": "r2", "ready": boot},
        ]
        return {"started": self.start_called, "devices": devices}

    async def start(self, *, lab_type: str, topology_path: str) -> dict:
        self.start_called = True
        self._ready_at = time.time()
        return {"ok": True}

    async def stop(self) -> dict:
        self.stop_called = True
        self._ready_at = None
        return {"ok": True}

    async def console_url(self, device: str) -> str:
        return f"ws://stub/console/{device}"


@pytest.mark.asyncio
async def test_full_lifecycle_to_lab_ready(monkeypatch):
    fake = _FakeLabAgent(devices_ready_in=0.2)
    monkeypatch.setattr(wf, "_lab_agent", fake)
    # also patch the lab_agent_client used by api/health
    from app.api import health as api_health
    from app.lab_agent_client import client as lac

    monkeypatch.setattr(api_health, "LabAgentClient", lambda: fake)

    lab_id = "lab-e2e01"
    async with SessionLocal() as db:
        db.add(
            Lab(
                id=lab_id,
                lab_type="ospf",
                golden_image="ospf.qcow2",
                status=LabStatus.REQUESTED.value,
                cpu=2,
                memory_mb=2048,
            )
        )
        await db.commit()

    await wf.run_lab(lab_id, lab_type="ospf", cpu=2, memory_mb=2048)

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
async def test_destroy_cleans_up(monkeypatch):
    fake = _FakeLabAgent()
    monkeypatch.setattr(wf, "_lab_agent", fake)

    lab_id = "lab-e2e02"
    async with SessionLocal() as db:
        db.add(
            Lab(
                id=lab_id,
                lab_type="bgp",
                golden_image="bgp.qcow2",
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
async def test_device_boot_timeout_marks_failed(monkeypatch):
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
                lab_type="ospf",
                golden_image="ospf.qcow2",
                status=LabStatus.REQUESTED.value,
                cpu=2,
                memory_mb=2048,
            )
        )
        await db.commit()

    # run_lab catches the timeout exception internally and marks FAILED + cleans up.
    await wf.run_lab(lab_id, lab_type="ospf", cpu=2, memory_mb=2048)

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
    assert lab.status in {LabStatus.FAILED.value, LabStatus.DESTROYED.value}
    assert lab.error and "HEALTH_CHECK_TIMEOUT" in lab.error
