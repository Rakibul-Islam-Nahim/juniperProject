"""IPAM Service FastAPI application.

Owns the `ip_allocations` table exclusively. The Backend Agent never touches
this table directly — it goes through the HTTP API exposed here.

On startup, the service ensures the `ip_allocations` table exists by running
`Base.metadata.create_all`. In production you'd point this at a dedicated
Alembic migration; for the MVP a single table with no relationships can be
created in-place.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import service as svc
from app.config import get_settings
from app.db import Base, SessionLocal, engine


# --- logging -----------------------------------------------------------------

def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


_log = structlog.get_logger("ipam")


# --- Pydantic schemas --------------------------------------------------------

class AllocateRequest(BaseModel):
    lab_id: str


class AllocateResponse(BaseModel):
    subnet: str
    gateway: str
    vm_ip: str


class StatusResponse(BaseModel):
    pool: str
    prefix_len: int
    total_blocks: int
    in_use: int
    free: int


class ReleaseResponse(BaseModel):
    ok: bool


# --- Service singleton -------------------------------------------------------

_ipam_svc: svc.IPAMService | None = None


def get_ipam_service() -> svc.IPAMService:
    global _ipam_svc
    if _ipam_svc is None:
        _ipam_svc = svc.IPAMService.from_settings()
    return _ipam_svc


# --- DB dependency -----------------------------------------------------------

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


# --- App ---------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    # Ensure the ip_allocations table exists. Safe to call repeatedly.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _log.info(
        "ipam.startup",
        app=settings.app_name,
        pool=settings.ipam_pool,
        prefix_len=settings.ipam_prefix_len,
        port=settings.service_port,
    )
    yield
    _log.info("ipam.shutdown")


app = FastAPI(title="Lab IPAM Service", version="0.1.0", lifespan=lifespan)


# --- Routes ------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(db: AsyncSession = Depends(get_db)) -> dict:
    try:
        from sqlalchemy import text

        await db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        raise HTTPException(status_code=503, detail={"error": {"code": "DB_DOWN", "message": "ipam db unreachable"}})


@app.post("/allocate", response_model=AllocateResponse)
async def allocate(req: AllocateRequest, db: AsyncSession = Depends(get_db)) -> AllocateResponse:
    ipam = get_ipam_service()
    try:
        result = await ipam.allocate(db, req.lab_id)
    except svc.IPAMExhausted as e:
        # 503 = service unavailable (pool exhausted); Backend should fail the lab with IPAM_EXHAUSTED.
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "IPAM_EXHAUSTED", "message": str(e)}},
        )
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        _log.exception("ipam.allocate.error", error=str(e))
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL", "message": "allocate failed"}})
    await db.commit()
    _log.info("ipam.allocated", lab_id=req.lab_id, subnet=result.subnet, vm_ip=result.vm_ip)
    return AllocateResponse(subnet=result.subnet, gateway=result.gateway, vm_ip=result.vm_ip)


@app.post("/release/{lab_id}", response_model=ReleaseResponse)
async def release(lab_id: str, db: AsyncSession = Depends(get_db)) -> ReleaseResponse:
    ipam = get_ipam_service()
    try:
        await ipam.hard_release(db, lab_id)
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        _log.exception("ipam.release.error", error=str(e))
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL", "message": "release failed"}})
    await db.commit()
    _log.info("ipam.released", lab_id=lab_id)
    return ReleaseResponse(ok=True)


@app.get("/status", response_model=StatusResponse)
async def status(db: AsyncSession = Depends(get_db)) -> StatusResponse:
    ipam = get_ipam_service()
    settings = get_settings()
    active = await ipam.list_active(db)
    in_use = len(active)
    return StatusResponse(
        pool=settings.ipam_pool,
        prefix_len=settings.ipam_prefix_len,
        total_blocks=ipam.total_blocks(),
        in_use=in_use,
        free=ipam.total_blocks() - in_use,
    )


@app.get("/allocations")
async def allocations(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Debug-only: list all active allocations."""
    active = await get_ipam_service().list_active(db)
    return [
        {
            "lab_id": a.lab_id,
            "subnet": a.subnet,
            "gateway": a.gateway,
            "vm_ip": a.vm_ip,
            "allocated_at": a.allocated_at.isoformat() if a.allocated_at else None,
        }
        for a in active
    ]


# --- Entrypoint for `python -m app.main` -----------------------------------

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.service_host, port=s.service_port, reload=False)