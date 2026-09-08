"""Shared pytest fixtures.

Tests use a fresh file-backed SQLite database (file in tmp) and the mock hypervisor so the
full lifecycle is exercisable without Docker / Cloud Hypervisor / a real Lab Agent.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Force test-friendly env BEFORE importing the app.
_tmpdir = tempfile.mkdtemp(prefix="labplatform-tests-")
_sqlite_path = str(Path(_tmpdir) / "test.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_sqlite_path}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{_sqlite_path}"
os.environ.setdefault("HYPERVISOR_BACKEND", "mock")
os.environ.setdefault("IPAM_POOL", "172.30.0.0/24")
os.environ.setdefault("IPAM_PREFIX_LEN", "28")
os.environ.setdefault("DEVICE_READINESS_TIMEOUT_SEC", "5")
os.environ.setdefault("DEVICE_READINESS_POLL_SEC", "1")
os.environ.setdefault("MAX_LABS_PER_HOST", "100")

# IMPORTANT: Settings is lru_cached, so clear it before app modules import.
from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

# Make sure the IPAM HTTP client in tests points at an unused port. Tests that
# need real allocation behaviour patch `wf._ipam_client` directly.
os.environ.setdefault("IPAM_SERVICE_URL", "http://localhost:8100")

from app import db as app_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.orchestrator import workflow as wf  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    """Session-scoped file-backed SQLite — created once, reused across tests.

    Tests that need a clean slate should issue ``DELETE FROM labs; DELETE FROM
    ip_allocations; DELETE FROM lab_events`` in setup.
    """
    url = os.environ["DATABASE_URL"]
    test_engine = create_async_engine(url, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine):
    """Truncate all tables before each test for isolation."""
    async with engine.begin() as conn:
        from sqlalchemy import text

        await conn.execute(text("DELETE FROM lab_events"))
        await conn.execute(text("DELETE FROM ip_allocations"))
        await conn.execute(text("DELETE FROM labs"))
    yield


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# Modules that imported `from app.db import SessionLocal` — patch every consumer too.
_DB_CONSUMERS = [
    "app.api.labs",
    "app.api.health",
    "app.events.service",
    "app.ipam.service",
    "app.models",
    "app.resources.manager",
    "app.terminal.gateway",
    "app.orchestrator.workflow",
]


@pytest_asyncio.fixture(autouse=True)
async def _patch_app_engine(monkeypatch, engine):
    """Swap app.db's engine/SessionLocal for the test engine across all consumers."""
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", factory)
    for mod_name in _DB_CONSUMERS:
        try:
            mod = __import__(mod_name, fromlist=["SessionLocal"])
        except Exception:
            continue
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", factory)
    yield


@pytest_asyncio.fixture(autouse=True)
async def _patch_ipam_client(monkeypatch):
    """Replace the real IPAM HTTP client with an in-memory fake for every test.

    Tests that exercise `wf.run_lab` / `wf.destroy_lab` indirectly (via the
    REST API) would otherwise try to POST to a real http://localhost:8100
    service which isn't running. Tests that need to inspect IPAM state can
    access the fake via `monkeypatch` or the `fake_ipam` fixture in
    test_e2e_workflow.py.
    """
    import ipaddress

    from app.ipam_client.client import AllocationResult

    class _BuiltinFakeIPAM:
        def __init__(self) -> None:
            self._net = ipaddress.ip_network("172.30.0.0/24", strict=False)
            self._blocks = [str(b) for b in self._net.subnets(new_prefix=28)]
            self._next = 0

        async def allocate(self, lab_id: str) -> AllocationResult:
            if self._next >= len(self._blocks):
                raise IPAMUnavailable_for_test("ipam exhausted in test fake")
            subnet = self._blocks[self._next]
            self._next += 1
            net = ipaddress.ip_network(subnet, strict=False)
            return AllocationResult(
                subnet=subnet,
                gateway=str(net.network_address + 1),
                vm_ip=str(net.network_address + 10),
            )

        async def release(self, lab_id: str) -> None:
            return None  # best-effort, idempotent

    class IPAMUnavailable_for_test(RuntimeError):
        pass

    fake = _BuiltinFakeIPAM()
    monkeypatch.setattr(wf, "_ipam_client", fake)
    yield


@pytest_asyncio.fixture
async def app():
    return create_app()


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

