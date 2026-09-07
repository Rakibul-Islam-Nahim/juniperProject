"""State transition + event recording helpers."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Lab, LabEvent
from app.state_machine import LabStatus, assert_transition

_log = get_logger("events")


async def transition_status(
    db: AsyncSession,
    lab: Lab,
    target: LabStatus,
    *,
    message: Optional[str] = None,
) -> None:
    """Move `lab` to `target`, asserting the transition is legal and writing an event row."""
    current = LabStatus(lab.status)
    assert_transition(current, target)

    await db.execute(
        update(Lab)
        .where(Lab.id == lab.id)
        .values(status=target.value)
    )
    lab.status = target.value

    # Special timestamps
    now = dt.datetime.now(dt.timezone.utc)
    if target is LabStatus.VM_STARTING and lab.started_at is None:
        lab.started_at = now
        await db.execute(update(Lab).where(Lab.id == lab.id).values(started_at=now))
    if target is LabStatus.LAB_READY and lab.ready_at is None:
        lab.ready_at = now
        await db.execute(update(Lab).where(Lab.id == lab.id).values(ready_at=now))
    if target in (LabStatus.DESTROYED, LabStatus.FAILED) and lab.terminated_at is None:
        lab.terminated_at = now
        await db.execute(update(Lab).where(Lab.id == lab.id).values(terminated_at=now))

    await record_event(db, lab.id, from_status=current.value, to_status=target.value, message=message)

    _log.info(
        "transition",
        from_status=current.value,
        to_status=target.value,
        message=message,
    )


async def record_event(
    db: AsyncSession,
    lab_id: str,
    *,
    from_status: Optional[str] = None,
    to_status: str,
    message: Optional[str] = None,
) -> None:
    db.add(LabEvent(lab_id=lab_id, from_status=from_status, to_status=to_status, message=message))
    await db.flush()
