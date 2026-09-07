"""Device readiness — real TCP probe against each device's management IP."""
from __future__ import annotations

import asyncio

from app import state as state_mod
from app.config import get_settings
from app.logging import get_logger

_log = get_logger("readiness")


async def _tcp_check(ip: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except (asyncio.TimeoutError, OSError):
        return False


async def readiness_loop(stop_event: asyncio.Event) -> None:
    s = get_settings()
    while not stop_event.is_set():
        devices = await state_mod.STORE.devices_snapshot()
        for d in devices:
            if d.ready:
                continue
            ok = await _tcp_check(d.mgmt_ip, s.readiness_check_port, s.readiness_connect_timeout_sec)
            if ok:
                _log.info("device.ready", device=d.name, mgmt_ip=d.mgmt_ip, port=s.readiness_check_port)
            await state_mod.STORE.mark_ready(d.name, ok)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=s.readiness_poll_interval_sec)
        except asyncio.TimeoutError:
            pass
