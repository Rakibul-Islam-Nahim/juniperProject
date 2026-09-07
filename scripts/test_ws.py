"""Quick WebSocket terminal smoke test."""
import asyncio
import sys

import websockets


async def main(lab_id: str) -> None:
    uri = f"ws://localhost:8000/api/v1/labs/{lab_id}/terminal?device=r1"
    async with websockets.connect(uri) as ws:
        banner = await ws.recv()
        print("RECV:", repr(banner[:160]))
        await ws.send("show interfaces terse")
        echo = await asyncio.wait_for(ws.recv(), timeout=5.0)
        print("RECV:", repr(echo[:160]))
        # wait for any final server-pushed message
        try:
            tail = await asyncio.wait_for(ws.recv(), timeout=2.0)
            print("RECV:", repr(tail[:160]))
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosedError):
            pass


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
