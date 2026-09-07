"""Liveness and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.db import engine
from app.lab_agent_client.client import LabAgentClient

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    db_ok = True
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text

            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    lab_agent_ok = await LabAgentClient.from_settings().health()
    if db_ok and lab_agent_ok:
        return {"status": "ready", "database": "ok", "lab_agent": "ok"}
    return {"status": "degraded", "database": "ok" if db_ok else "down", "lab_agent": "ok" if lab_agent_ok else "down"}