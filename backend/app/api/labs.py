"""Labs REST endpoints."""
from __future__ import annotations

import asyncio
import re
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.db import SessionLocal
from app.deps import DBSession
from app.models import Lab
from app.orchestrator import destroy_lab, run_lab, track_task
from app.registry import get as get_lab_type_config
from app.resources.manager import QuotaExceeded
from app.schemas import CreateLabRequest, ErrorResponse, LabResponse
from app.state_machine import LabStatus, progress_pct
from app.logging import get_logger

_log = get_logger("api.labs")
router = APIRouter(prefix="/api/v1/labs", tags=["labs"])

_LAB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _to_response(lab: Lab) -> LabResponse:
    return LabResponse(
        lab_id=lab.id,
        user_id=lab.user_id,
        lab_type=lab.lab_type,
        golden_image=lab.golden_image,
        status=lab.status,
        progress=progress_pct(LabStatus(lab.status)),
        cpu=lab.cpu,
        memory_mb=lab.memory_mb,
        subnet=lab.subnet,
        gateway=lab.gateway,
        vm_ip=lab.vm_ip,
        vm_pid=lab.vm_pid,
        tap_name=lab.tap_name,
        error=lab.error,
        created_at=lab.created_at,
        started_at=lab.started_at,
        ready_at=lab.ready_at,
        terminated_at=lab.terminated_at,
    )


def _parse_memory(s: str) -> int:
    m = re.match(r"^(\d+)([MG])$", s)
    if not m:
        raise ValueError(f"invalid memory: {s}")
    n = int(m.group(1))
    return n * 1024 if m.group(2) == "G" else n


@router.post("", status_code=status.HTTP_201_CREATED, response_model=LabResponse)
async def create_lab(req: CreateLabRequest, db: DBSession) -> LabResponse:
    # Sanity check lab_type is registered.
    try:
        cfg = get_lab_type_config(req.lab_type)
    except KeyError as e:
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_LAB_TYPE", "message": str(e)}})

    memory_mb = _parse_memory(req.memory)

    lab_id = "lab-" + uuid.uuid4().hex[:12]
    lab = Lab(
        id=lab_id,
        user_id=req.user_id,
        lab_type=req.lab_type,
        golden_image=cfg.golden_image,
        status=LabStatus.REQUESTED.value,
        cpu=req.cpu,
        memory_mb=memory_mb,
    )
    db.add(lab)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # Kick off the async workflow as a background task.
    task = asyncio.create_task(run_lab(lab_id, lab_type=req.lab_type, cpu=req.cpu, memory_mb=memory_mb))
    track_task(lab_id, task)

    await db.refresh(lab)
    return _to_response(lab)


@router.get("", response_model=list[LabResponse])
async def list_labs(
    db: DBSession,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[LabResponse]:
    stmt = select(Lab).order_by(Lab.created_at.desc()).limit(limit)
    if status_filter:
        stmt = (
            select(Lab)
            .where(Lab.status == status_filter.upper())
            .order_by(Lab.created_at.desc())
            .limit(limit)
        )
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_response(r) for r in rows]


@router.get("/{lab_id}", response_model=LabResponse)
async def get_lab(lab_id: str, db: DBSession) -> LabResponse:
    if not _LAB_ID_RE.match(lab_id):
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_ID", "message": "invalid lab id"}})
    lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one_or_none()
    if lab is None:
        raise HTTPException(
            status_code=404, detail={"error": {"code": "LAB_NOT_FOUND", "message": f"no lab {lab_id}"}}
        )
    return _to_response(lab)


@router.delete("/{lab_id}", status_code=status.HTTP_202_ACCEPTED, response_model=LabResponse)
async def delete_lab(lab_id: str, db: DBSession) -> LabResponse:
    if not _LAB_ID_RE.match(lab_id):
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_ID", "message": "invalid lab id"}})
    lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one_or_none()
    if lab is None:
        raise HTTPException(
            status_code=404, detail={"error": {"code": "LAB_NOT_FOUND", "message": f"no lab {lab_id}"}}
        )
    if LabStatus(lab.status) in (LabStatus.DESTROYED,):
        return _to_response(lab)

    task = asyncio.create_task(destroy_lab(lab_id))
    track_task(lab_id, task)
    await db.refresh(lab)
    return _to_response(lab)
