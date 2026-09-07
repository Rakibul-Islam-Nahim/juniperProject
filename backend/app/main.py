"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import health_router, labs_router, ws_router
from app.config import get_settings
from app.logging import configure_logging, get_logger
from app.orchestrator import register_signal_handlers

_log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    _log.info("startup", app=settings.app_name, hypervisor=settings.hypervisor_backend)
    try:
        register_signal_handlers(__import__("asyncio").get_running_loop())
    except RuntimeError:
        pass
    yield
    _log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Lab Platform Backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_req: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_json_safe(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "request validation failed",
                        "details": exc.errors(),
                    }
                }
            ),
        )

    @app.exception_handler(HTTPException)
    async def _http_handler(_req: Request, exc: HTTPException):
        # Pass through dict-style detail envelopes as-is, otherwise wrap.
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            body = exc.detail
        else:
            body = {"error": {"code": _code_for_status(exc.status_code), "message": str(exc.detail)}}
        return JSONResponse(status_code=exc.status_code, content=_json_safe(body))

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):
        _log.exception("unhandled.exception", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "internal server error"}},
        )

    app.include_router(health_router)
    app.include_router(labs_router)
    app.include_router(ws_router)
    return app


def _code_for_status(code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
    }.get(code, "ERROR")


def _json_safe(obj):
    """Recursively convert non-JSON-serialisable values to strings."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    try:
        import json

        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


app = create_app()
