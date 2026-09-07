"""WebSocket terminal gateway.

Bridges the student's browser WebSocket to the in-VM Lab Agent's console WebSocket
(`/console/{device}`). The Lab Agent itself proxies to the container console.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.logging import get_logger, lab_id_var
from app.models import Lab
from app.state_machine import LabStatus

_log = get_logger("terminal")
_settings = get_settings()


async def terminal_websocket(websocket: WebSocket, lab_id: str, device: Optional[str] = None) -> None:
    """FastAPI WebSocket endpoint. `device` comes from the path in the route."""
    token = lab_id_var.set(lab_id)
    try:
        # Validate lab state before accepting
        async with SessionLocal() as db:
            lab = (await db.execute(select(Lab).where(Lab.id == lab_id))).scalar_one_or_none()
            if lab is None:
                await websocket.close(code=4404, reason="lab not found")
                return
            if LabStatus(lab.status) is not LabStatus.LAB_READY:
                await websocket.close(
                    code=4409, reason=f"lab not ready (status={lab.status})"
                )
                return

        upstream_url = (
            f"{_settings.lab_agent_base_url.replace('http', 'ws', 1)}/console/{device}"
            if device
            else f"{_settings.lab_agent_base_url.replace('http', 'ws', 1)}/console"
        )
        _log.info("terminal.connect", device=device, upstream=upstream_url)
        await websocket.accept()

        async with websockets.connect(upstream_url, open_timeout=10, ping_interval=20) as upstream:
            client_to_upstream = asyncio.create_task(_pump(websocket, upstream, label="student->vm"))
            upstream_to_client = asyncio.create_task(_pump(upstream, websocket, label="vm->student"))
            done, pending = await asyncio.wait(
                {client_to_upstream, upstream_to_client}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
    except WebSocketDisconnect:
        _log.info("terminal.disconnect")
    except Exception as e:  # noqa: BLE001
        _log.warning("terminal.error", error=str(e))
        try:
            await websocket.close(code=1011, reason="internal error")
        except Exception:  # noqa: BLE001
            pass
    finally:
        lab_id_var.reset(token)


def _is_websockets_client(obj) -> bool:
    """Detect websockets-client Connection (v12+ unified send() API)."""
    return hasattr(obj, "send") and hasattr(obj, "recv") and not hasattr(obj, "send_text")


def _is_fastapi_ws(obj) -> bool:
    """Detect FastAPI / Starlette WebSocket (send_text / send_bytes)."""
    return hasattr(obj, "send_text") and hasattr(obj, "send_bytes")


async def _send(dst, msg) -> None:
    """Send a message to either a FastAPI WebSocket or a websockets-client connection.

    FastAPI WS uses send_text / send_bytes; websockets v12+ uses unified send().
    """
    is_bytes = isinstance(msg, (bytes, bytearray))
    if _is_fastapi_ws(dst):
        if is_bytes:
            await dst.send_bytes(msg)
        else:
            await dst.send_text(msg)
    else:
        # websockets-client
        await dst.send(msg)


async def _pump(src, dst, *, label: str) -> None:
    """Pump messages from `src` to `dst`.

    Supports both `websockets.client.WebSocketClientProtocol` (which implements
    `__aiter__`) and FastAPI `WebSocket` (which exposes `receive_text/bytes`).
    """
    # websockets-client uses __aiter__; FastAPI WebSocket does not.
    if hasattr(src, "__aiter__"):
        try:
            async for msg in src:
                await _send(dst, msg)
        except Exception as e:  # noqa: BLE001
            _log.info("terminal.pump.end", label=label, error=str(e))
        return

    # FastAPI WebSocket — drive via receive_text / receive_bytes.
    while True:
        try:
            msg = await src.receive()
        except WebSocketDisconnect:
            return
        except Exception as e:  # noqa: BLE001
            _log.info("terminal.pump.end", label=label, error=str(e))
            return

        mtype = msg.get("type") if isinstance(msg, dict) else None
        if mtype == "websocket.disconnect":
            return
        if "text" in msg and msg["text"] is not None:
            await _send(dst, msg["text"])
        elif "bytes" in msg and msg["bytes"] is not None:
            await _send(dst, msg["bytes"])
