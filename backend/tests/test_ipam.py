from __future__ import annotations

import pytest

from app.ipam.service import IPAMExhausted, IPAMService


@pytest.mark.asyncio
async def test_allocate_returns_distinct_subnets(session_factory):
    ipam = IPAMService("172.30.0.0/24", 28)  # pool /24, blocks /28 → 16 subnets
    async with session_factory() as db:
        a = await ipam.allocate(db, "lab-1")
        b = await ipam.allocate(db, "lab-2")
    assert a.subnet != b.subnet
    assert a.subnet.endswith("/28")
    assert a.gateway.endswith(".1")
    assert a.vm_ip.endswith(".10")


@pytest.mark.asyncio
async def test_allocate_then_release_then_reuse(session_factory):
    ipam = IPAMService("172.30.0.0/28", 29)  # 2 subnets in /28
    async with session_factory() as db:
        a = await ipam.allocate(db, "lab-1")
        b = await ipam.allocate(db, "lab-2")
        with pytest.raises(IPAMExhausted):
            await ipam.allocate(db, "lab-3")
        await ipam.hard_release(db, "lab-1")
        c = await ipam.allocate(db, "lab-3")
    assert c.subnet == a.subnet


@pytest.mark.asyncio
async def test_soft_release_marks_row(session_factory):
    ipam = IPAMService("172.30.0.0/28", 29)
    async with session_factory() as db:
        a = await ipam.allocate(db, "lab-1")
        await ipam.release(db, "lab-1")
        active = await ipam.list_active(db)
    assert all(row.subnet != a.subnet for row in active)
