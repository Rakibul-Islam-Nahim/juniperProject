"""Lab Agent HTTP routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app import state as state_mod
from app.config import get_settings
from app.containerlab import ContainerlabWrapper
from app.logging import get_logger

_log = get_logger("routes")
router = APIRouter()

CLAB = ContainerlabWrapper.from_settings()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "topology": get_settings().topology_path}


@router.get("/status")
async def status() -> dict:
    return await state_mod.STORE.snapshot()


@router.post("/start")
async def start() -> dict:
    """Manual re-deploy trigger — kept for operator/dev use. The backend no
    longer calls this; deploy happens automatically on lab_agent startup."""
    try:
        await CLAB.deploy()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"deploy failed: {e}")
    return await state_mod.STORE.snapshot()


@router.post("/stop")
async def stop() -> dict:
    try:
        await CLAB.destroy()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"destroy failed: {e}")
    return {"ok": True}


@router.websocket("/console")
async def console_default(websocket: WebSocket) -> None:
    await _console(websocket, device=None)


@router.websocket("/console/{device}")
async def console_device(websocket: WebSocket, device: str) -> None:
    await _console(websocket, device=device)


async def _console(websocket: WebSocket, device: Optional[str]) -> None:
    await websocket.accept()
    snap = await state_mod.STORE.snapshot()
    if not snap.get("started"):
        await websocket.send_text("[lab-agent] lab not started\r\n")
        await websocket.close(code=4400)
        return
    target = device or (snap["devices"][0]["name"] if snap["devices"] else "unknown")
    await websocket.send_text(f"\r\n*** console for {target} — not yet wired to a real PTY ***\r\n")
    try:
        while True:
            msg = await websocket.receive()
            if "text" in msg and msg["text"].strip().lower() in ("exit", "quit"):
                await websocket.send_text("\r\nlogout\r\n")
                break
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001
        _log.warning("console.error", error=str(e))
