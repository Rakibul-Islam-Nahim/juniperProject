"""The orchestrator workflow.

Drives a single lab from REQUESTED to LAB_READY (creation) or from any state through
DESTROYED (cleanup). All transitions go through `events.transition_status` so the state
machine + LabEvent log stay consistent.

The orchestrator is intentionally linear and synchronous-with-await — no nested tasks,
no background loops. A lab is one coroutine from creation; a separate coroutine handles
destruction. This makes failure semantics simple: any exception triggers cleanup.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import signal
import time
from typing import Optional

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.events.service import record_event, transition_status
from app.hypervisor.base import HypervisorBackend, VMHandle, VMStartError
from app.hypervisor.factory import build_backend
from app.ipam_client.client import IPAMClient, IPAMExhausted, IPAMUnavailable
from app.lab_agent_client.client import LabAgentClient, LabAgentError
from app.logging import get_logger, lab_id_var
from app.models import Lab
from app.networking.service import NetworkingService
from app.registry import get as get_lab_type_config
from app.resources.manager import QuotaExceeded, ResourceManager
from app.state_machine import FailureReason, LabStatus

_log = get_logger("orchestrator")

# Module-level singletons — built once at startup.
_settings: Settings = get_settings()
_ipam_client: IPAMClient = IPAMClient.from_settings()
_resources: ResourceManager = ResourceManager.from_settings()
_networking: NetworkingService = NetworkingService.from_settings()
_hypervisor: HypervisorBackend = build_backend(_settings)
_lab_agent: LabAgentClient = LabAgentClient.from_settings()

# Track running tasks so the API can await shutdown gracefully.
_RUNNING_TASKS: dict[str, asyncio.Task] = {}
_BACKEND_VM_HANDLES: dict[str, VMHandle] = {}


def get_vm_handle(lab_id: str) -> Optional[VMHandle]:
    return _BACKEND_VM_HANDLES.get(lab_id)


async def run_lab(lab_id: str, *, lab_type: str, cpu: int, memory_mb: int) -> None:
    """Create a lab end-to-end. Returns when the lab is LAB_READY or FAILED."""
    token = lab_id_var.set(lab_id)
    try:
        await _run_lab_impl(lab_id, lab_type=lab_type, cpu=cpu, memory_mb=memory_mb)
    except asyncio.CancelledError:
        _log.warning("orchestrator.cancelled")
        raise
    except Exception as e:  # noqa: BLE001
        _log.exception("orchestrator.unhandled", error=str(e))
        await _mark_failed_and_cleanup(lab_id, reason=FailureReason.VM_START_FAILED, message=str(e))
    finally:
        lab_id_var.reset(token)


async def _run_lab_impl(lab_id: str, *, lab_type: str, cpu: int, memory_mb: int) -> None:
    cfg = get_lab_type_config(lab_type)
    _log.info("lab.start", lab_type=lab_type, cpu=cpu, memory_mb=memory_mb, image=cfg.golden_image)

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        await _resources.can_admit(db, cpu=cpu, memory_mb=memory_mb)
        await transition_status(db, lab, LabStatus.CREATING, message="resources checked")
        await db.commit()

    # IP allocation — delegated to the standalone ipam-service over HTTP.
    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        try:
            alloc = await _ipam_client.allocate(lab_id)
        except IPAMExhausted as e:
            await transition_status(db, lab, LabStatus.FAILED, message=str(e))
            await db.commit()
            raise
        except IPAMUnavailable as e:
            _log.error("ipam.unavailable.on_allocate", error=str(e))
            await transition_status(
                db, lab, LabStatus.FAILED,
                message=f"IPAM service unavailable: {e}",
            )
            await db.commit()
            raise

        lab.subnet = alloc.subnet
        lab.gateway = alloc.gateway
        lab.vm_ip = alloc.vm_ip
        lab.tap_name = _tap_name(lab_id)
        await db.execute(
            Lab.__table__.update()
            .where(Lab.id == lab_id)
            .values(subnet=alloc.subnet, gateway=alloc.gateway, vm_ip=alloc.vm_ip, tap_name=lab.tap_name)
        )
        await transition_status(db, lab, LabStatus.NETWORK_ALLOCATED, message=f"subnet={alloc.subnet}")
        await db.commit()

    # TAP setup
    try:
        await _networking.create_tap_and_attach(lab.tap_name)
    except Exception as e:  # noqa: BLE001
        await _mark_failed_and_cleanup(
            lab_id, reason=FailureReason.NETWORK_CONFIGURATION_FAILED, message=str(e)
        )
        return

    # Start VM
    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        await transition_status(db, lab, LabStatus.VM_STARTING, message="launching microVM")
        await db.commit()

    try:
        handle = await _hypervisor.start(
            lab_id=lab_id,
            image=cfg.golden_image,
            cpu=cpu,
            memory_mb=memory_mb,
            tap_name=lab.tap_name,
            vm_ip=lab.vm_ip,
            gateway=lab.gateway,
            subnet=lab.subnet,
        )
        _BACKEND_VM_HANDLES[lab_id] = handle
    except VMStartError as e:
        await _mark_failed_and_cleanup(lab_id, reason=FailureReason.VM_START_FAILED, message=str(e))
        return

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        lab.vm_pid = handle.pid
        lab.vm_api_socket = handle.api_socket
        await db.execute(
            Lab.__table__.update()
            .where(Lab.id == lab_id)
            .values(vm_pid=handle.pid, vm_api_socket=handle.api_socket)
        )
        await db.commit()

    try:
        await _hypervisor.wait_ready(handle, timeout_sec=240.0)
    except Exception as e:  # noqa: BLE001
        await _mark_failed_and_cleanup(lab_id, reason=FailureReason.VM_START_FAILED, message=str(e))
        return

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        await transition_status(db, lab, LabStatus.VM_READY, message=f"pid={handle.pid}")
        await db.commit()

    # ContainerLab: lab_agent deploys automatically on its own startup now
    # (see lab_agent/app/main.py's lifespan hook) — one golden image = one
    # fixed lab, so there is no lab_type/topology_path to hand it over the
    # network anymore. We just record the transition and move straight to
    # polling /status for real device readiness.
    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        await transition_status(db, lab, LabStatus.CONTAINERLAB_STARTING, message="lab_agent auto-deploying")
        await db.commit()

    # Wait for device readiness
    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        await transition_status(db, lab, LabStatus.DEVICES_BOOTING, message="polling devices")
        await db.commit()

    if not await _wait_for_devices_ready(lab_id, lab.vm_ip, timeout_sec=_settings.device_readiness_timeout_sec):
        await _mark_failed_and_cleanup(
            lab_id,
            reason=FailureReason.HEALTH_CHECK_TIMEOUT,
            message=f"devices did not become ready within {_settings.device_readiness_timeout_sec}s",
        )
        return

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        await transition_status(db, lab, LabStatus.LAB_READY, message="all devices healthy")
        await db.commit()
    _log.info("lab.ready")


async def _wait_for_devices_ready(lab_id: str, vm_ip: str, *, timeout_sec: float) -> bool:
    # Per-lab client, scoped to this VM's own IP — NOT the module-level
    # `_lab_agent`, which is built once at startup from a static
    # LAB_AGENT_BASE_URL and can never be correct for more than one lab
    # (or, as happened here, can silently point at nothing at all if that
    # static value is stale/wrong). Mirrors how wait_ready() already
    # correctly uses handle.vm_ip for the VM-boot health check.
    agent = LabAgentClient(f"http://{vm_ip}:9001", timeout_sec=10.0)
    deadline = time.time() + timeout_sec
    poll = max(1.0, float(_settings.device_readiness_poll_sec))
    while time.time() < deadline:
        try:
            st = await agent.status()
        except LabAgentError as e:
            _log.warning("lab_agent.status.error", error=str(e))
            await asyncio.sleep(poll)
            continue

        devices = st.get("devices", [])
        if devices and all(d.get("ready") for d in devices):
            _log.info("devices.all_ready", count=len(devices))
            return True
        booting = [d.get("name") for d in devices if not d.get("ready")]
        _log.info("devices.booting", booting=booting, ready=sum(1 for d in devices if d.get("ready")))
        await asyncio.sleep(poll)
    return False


async def destroy_lab(lab_id: str) -> None:
    """Idempotent destroy — runs from any state and ends at DESTROYED."""
    token = lab_id_var.set(lab_id)
    try:
        await _destroy_lab_impl(lab_id)
    finally:
        lab_id_var.reset(token)


async def _destroy_lab_impl(lab_id: str) -> None:
    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one_or_none()
        if lab is None:
            return
        if LabStatus(lab.status) in (LabStatus.DESTROYED,):
            return
        await transition_status(db, lab, LabStatus.STOPPING, message="destroy requested")
        await db.commit()

    # Stop containerlab (best effort)
    try:
        await _lab_agent.stop()
    except LabAgentError as e:
        _log.warning("destroy.lab_agent.stop.error", error=str(e))

    # Stop VM
    handle = _BACKEND_VM_HANDLES.pop(lab_id, None)
    if handle is not None:
        try:
            await _hypervisor.stop(handle)
        except Exception as e:  # noqa: BLE001
            _log.warning("destroy.vm.stop.error", error=str(e))

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        await transition_status(db, lab, LabStatus.VM_STOPPED, message="vm stopped")
        await db.commit()

    # Networking teardown
    if lab.tap_name:
        try:
            await _networking.remove_tap(lab.tap_name)
        except Exception as e:  # noqa: BLE001
            _log.warning("destroy.tap.remove.error", error=str(e))

    # IPAM release — best-effort (no retries). A leaked subnet row can be
    # cleaned up later; we still proceed with the rest of destroy so the lab
    # always reaches DESTROYED even if ipam-service is unhealthy.
    try:
        await _ipam_client.release(lab_id)
    except Exception as e:  # noqa: BLE001
        _log.warning("destroy.ipam.release.error", error=str(e))

    async with SessionLocal() as db:
        await transition_status(db, lab, LabStatus.RESOURCES_RELEASED, message="ipam + tap released")
        await db.commit()

    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one()
        await transition_status(db, lab, LabStatus.DESTROYED, message="lab destroyed")
        await db.commit()
    _log.info("lab.destroyed")


async def _mark_failed_and_cleanup(lab_id: str, *, reason: FailureReason, message: str) -> None:
    async with SessionLocal() as db:
        lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one_or_none()
        if lab is None:
            return
        cur = LabStatus(lab.status)
        if cur not in (LabStatus.DESTROYED,):
            try:
                await transition_status(db, lab, LabStatus.FAILED, message=f"{reason.value}: {message}")
                lab.error = f"{reason.value}: {message}"
                await db.execute(Lab.__table__.update().where(Lab.id == lab_id).values(error=lab.error))
                await db.commit()
            except Exception as e:  # noqa: BLE001
                _log.exception("failed.marking.error", error=str(e))

    await cleanup_lab(lab_id)


async def cleanup_lab(lab_id: str) -> None:
    """Best-effort cleanup — runs even if the lab is already FAILED."""
    try:
        await _destroy_lab_impl(lab_id)
    except Exception as e:  # noqa: BLE001
        _log.exception("cleanup.failed", error=str(e))


def _tap_name(lab_id: str) -> str:
    return f"{_settings.ch_tap_prefix}-{lab_id[:11]}"


# ---------- process lifecycle helpers ----------

def register_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))
        except (NotImplementedError, RuntimeError):
            # Windows or non-main thread — ignore
            pass


async def shutdown(sig: Optional[int] = None) -> None:
    _log.info("orchestrator.shutdown.begin", signal=sig)
    if _RUNNING_TASKS:
        await asyncio.gather(*_RUNNING_TASKS.values(), return_exceptions=True)
    _log.info("orchestrator.shutdown.done")


def track_task(lab_id: str, task: asyncio.Task) -> None:
    _RUNNING_TASKS[lab_id] = task
    task.add_done_callback(lambda _t, lid=lab_id: _RUNNING_TASKS.pop(lid, None))