"""WebSocket route for the terminal gateway."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket

from app.terminal.gateway import terminal_websocket

router = APIRouter(tags=["ws"])


@router.websocket("/api/v1/labs/{lab_id}/terminal")
async def ws_terminal(websocket: WebSocket, lab_id: str, device: str | None = None) -> None:
    await terminal_websocket(websocket, lab_id, device=device)
