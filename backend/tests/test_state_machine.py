from __future__ import annotations

import pytest

from app.state_machine import (
    InvalidTransitionError,
    LabStatus,
    assert_transition,
    can_transition,
    progress_pct,
)


def test_terminal_statuses():
    assert LabStatus.DESTROYED in {LabStatus.DESTROYED, LabStatus.FAILED}
    # can't transition out of destroyed
    assert not can_transition(LabStatus.DESTROYED, LabStatus.LAB_READY)


def test_happy_path_transitions():
    chain = [
        LabStatus.REQUESTED,
        LabStatus.CREATING,
        LabStatus.NETWORK_ALLOCATED,
        LabStatus.VM_STARTING,
        LabStatus.VM_READY,
        LabStatus.CONTAINERLAB_STARTING,
        LabStatus.DEVICES_BOOTING,
        LabStatus.LAB_READY,
    ]
    for cur, nxt in zip(chain, chain[1:]):
        assert can_transition(cur, nxt), f"expected {cur} -> {nxt}"


def test_illegal_transition():
    with pytest.raises(InvalidTransitionError):
        assert_transition(LabStatus.LAB_READY, LabStatus.CREATING)


def test_failed_can_be_cleaned_up():
    # a failed lab can transition STOPPING for cleanup
    assert can_transition(LabStatus.FAILED, LabStatus.STOPPING)


def test_progress_monotonic():
    chain = [
        LabStatus.REQUESTED,
        LabStatus.CREATING,
        LabStatus.NETWORK_ALLOCATED,
        LabStatus.VM_STARTING,
        LabStatus.VM_READY,
        LabStatus.CONTAINERLAB_STARTING,
        LabStatus.DEVICES_BOOTING,
        LabStatus.LAB_READY,
    ]
    pcts = [progress_pct(s) for s in chain]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100
