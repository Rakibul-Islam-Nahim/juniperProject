"""Lab Agent FastAPI application."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.containerlab import ContainerlabWrapper
from app.logging import configure_logging, get_logger
from app.readiness import readiness_loop
from app.routes import router

_log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    _log.info("lab_agent.startup", host=settings.agent_host, port=settings.agent_port,
               topology=settings.topology_path)

    # One golden image = one fixed lab. Deploy it automatically on startup
    # rather than waiting for an external trigger — removes a network round
    # trip (backend -> lab_agent POST /start) from the critical boot path.
    clab = ContainerlabWrapper.from_settings()
    try:
        await clab.deploy()
        _log.info("lab_agent.autodeploy.ok")
    except Exception as e:  # noqa: BLE001
        _log.error("lab_agent.autodeploy.failed", error=str(e))
        # Do not crash the whole agent — /health still answers, and /status
        # will show zero ready devices, which the backend's readiness poll
        # will correctly time out on rather than hang forever silently.

    stop_event = asyncio.Event()
    task = asyncio.create_task(readiness_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await asyncio.wait([task], timeout=2.0)


def create_app() -> FastAPI:
    app = FastAPI(title="Lab Agent", version="0.2.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run("app.main:app", host=s.agent_host, port=s.agent_port, reload=False)
