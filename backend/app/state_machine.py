"""Lab lifecycle state machine.

Centralised so that the orchestrator, API layer, and tests use a single source of truth.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING


class LabStatus(str, Enum):
    REQUESTED = "REQUESTED"
    CREATING = "CREATING"
    NETWORK_ALLOCATED = "NETWORK_ALLOCATED"
    VM_STARTING = "VM_STARTING"
    VM_READY = "VM_READY"
    CONTAINERLAB_STARTING = "CONTAINERLAB_STARTING"
    DEVICES_BOOTING = "DEVICES_BOOTING"
    LAB_READY = "LAB_READY"
    STOPPING = "STOPPING"
    VM_STOPPED = "VM_STOPPED"
    RESOURCES_RELEASED = "RESOURCES_RELEASED"
    DESTROYED = "DESTROYED"
    FAILED = "FAILED"


class FailureReason(str, Enum):
    VM_START_FAILED = "VM_START_FAILED"
    CONTAINERLAB_FAILED = "CONTAINERLAB_FAILED"
    DEVICE_BOOT_FAILED = "DEVICE_BOOT_FAILED"
    HEALTH_CHECK_TIMEOUT = "HEALTH_CHECK_TIMEOUT"
    NETWORK_CONFIGURATION_FAILED = "NETWORK_CONFIGURATION_FAILED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    LAB_AGENT_UNREACHABLE = "LAB_AGENT_UNREACHABLE"
    CLEANUP_FAILED = "CLEANUP_FAILED"


# Terminal statuses — no further transitions.
TERMINAL_STATUSES = {LabStatus.DESTROYED, LabStatus.FAILED}


# Allowed forward transitions (creation path).
_ALLOWED: dict[LabStatus, set[LabStatus]] = {
    LabStatus.REQUESTED: {LabStatus.CREATING, LabStatus.FAILED, LabStatus.STOPPING},
    LabStatus.CREATING: {
        LabStatus.NETWORK_ALLOCATED,
        LabStatus.FAILED,
        LabStatus.STOPPING,
    },
    LabStatus.NETWORK_ALLOCATED: {LabStatus.VM_STARTING, LabStatus.FAILED, LabStatus.STOPPING},
    LabStatus.VM_STARTING: {LabStatus.VM_READY, LabStatus.FAILED, LabStatus.STOPPING},
    LabStatus.VM_READY: {LabStatus.CONTAINERLAB_STARTING, LabStatus.FAILED, LabStatus.STOPPING},
    LabStatus.CONTAINERLAB_STARTING: {LabStatus.DEVICES_BOOTING, LabStatus.FAILED, LabStatus.STOPPING},
    LabStatus.DEVICES_BOOTING: {LabStatus.LAB_READY, LabStatus.FAILED, LabStatus.STOPPING},
    LabStatus.LAB_READY: {LabStatus.STOPPING, LabStatus.FAILED},
    LabStatus.STOPPING: {LabStatus.VM_STOPPED, LabStatus.FAILED},
    LabStatus.VM_STOPPED: {LabStatus.RESOURCES_RELEASED, LabStatus.FAILED},
    LabStatus.RESOURCES_RELEASED: {LabStatus.DESTROYED, LabStatus.FAILED},
    LabStatus.DESTROYED: set(),
    LabStatus.FAILED: {LabStatus.STOPPING},  # a failed lab can still be cleaned up
}


class InvalidTransitionError(RuntimeError):
    def __init__(self, current: LabStatus, target: LabStatus):
        super().__init__(f"Illegal transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: LabStatus, target: LabStatus) -> bool:
    return target in _ALLOWED.get(current, set())


def assert_transition(current: LabStatus, target: LabStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def progress_pct(status: LabStatus) -> int:
    """Linear-ish progress for the API (0–100)."""
    table: dict[LabStatus, int] = {
        LabStatus.REQUESTED: 5,
        LabStatus.CREATING: 10,
        LabStatus.NETWORK_ALLOCATED: 20,
        LabStatus.VM_STARTING: 35,
        LabStatus.VM_READY: 55,
        LabStatus.CONTAINERLAB_STARTING: 70,
        LabStatus.DEVICES_BOOTING: 85,
        LabStatus.LAB_READY: 100,
        LabStatus.STOPPING: 95,
        LabStatus.VM_STOPPED: 50,
        LabStatus.RESOURCES_RELEASED: 25,
        LabStatus.DESTROYED: 100,
        LabStatus.FAILED: 100,
    }
    return table.get(status, 0)


if TYPE_CHECKING:
    # avoid unused-import warning at runtime
    pass
