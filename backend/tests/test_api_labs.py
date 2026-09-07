from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.models import Lab
from app.orchestrator import workflow as wf


@pytest.mark.asyncio
async def test_create_lab_returns_201_and_lab_id(client):
    r = await client.post("/api/v1/labs", json={"lab_type": "ospf", "cpu": 4, "memory": "4G"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["lab_id"].startswith("lab-")
    assert body["status"] in {"REQUESTED", "CREATING"}  # background task may have started


@pytest.mark.asyncio
async def test_create_lab_invalid_type_422(client):
    r = await client.post("/api/v1/labs", json={"lab_type": "no-such", "cpu": 2, "memory": "2G"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_lab_bad_memory_422(client):
    r = await client.post("/api/v1/labs", json={"lab_type": "ospf", "cpu": 2, "memory": "huge"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_lab_404(client):
    r = await client.get("/api/v1/labs/lab-doesnotexist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "LAB_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_labs_includes_recently_created(client):
    a = (await client.post("/api/v1/labs", json={"lab_type": "ospf"})).json()
    b = (await client.post("/api/v1/labs", json={"lab_type": "bgp"})).json()
    r = await client.get("/api/v1/labs")
    assert r.status_code == 200
    ids = [lab["lab_id"] for lab in r.json()]
    assert a["lab_id"] in ids
    assert b["lab_id"] in ids
    assert len(ids) == 2


@pytest.mark.asyncio
async def test_health_endpoints(client):
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code in (200, 503)


@pytest.mark.asyncio
async def test_delete_lab_transitions_to_stopping(client):
    body = (await client.post("/api/v1/labs", json={"lab_type": "ospf"})).json()
    lab_id = body["lab_id"]
    # wait for the background task to settle the lab to a stable state
    import asyncio as _asyncio

    await _asyncio.sleep(0.5)
    r = await client.delete(f"/api/v1/labs/{lab_id}")
    assert r.status_code == 202, r.text
    # wait for destruction
    for _ in range(50):
        lab = (await client.get(f"/api/v1/labs/{lab_id}")).json()
        if lab["status"] in {"DESTROYED", "FAILED"}:
            break
        await _asyncio.sleep(0.2)
    final = (await client.get(f"/api/v1/labs/{lab_id}")).json()
    assert final["status"] in {"DESTROYED", "STOPPING", "VM_STOPPED", "RESOURCES_RELEASED"}
