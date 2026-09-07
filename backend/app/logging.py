"""Structured logging with a per-request lab_id contextvar."""
from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

lab_id_var: ContextVar[str | None] = ContextVar("lab_id", default=None)


def _add_lab_id(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    lid = lab_id_var.get()
    if lid is not None and "lab_id" not in event_dict:
        event_dict["lab_id"] = lid
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_lab_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ]
        if level.upper() == "DEBUG"
        else [
            structlog.contextvars.merge_contextvars,
            _add_lab_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name or "app")
