"""Test the lab agent console directly (no backend gateway)."""
import asyncio

import websockets


async def main() -> None:
    uri = "ws://localhost:9001/console/r1"
    async with websockets.connect(uri) as ws:
        banner = await ws.recv()
        print("RECV:", repr(banner[:160]))
        await ws.send("show interfaces terse")
        echo = await asyncio.wait_for(ws.recv(), timeout=5.0)
        print("RECV:", repr(echo[:160]))
        try:
            tail = await asyncio.wait_for(ws.recv(), timeout=2.0)
            print("RECV:", repr(tail[:160]))
        except asyncio.TimeoutError:
            print("(timeout)")


if __name__ == "__main__":
    asyncio.run(main())
