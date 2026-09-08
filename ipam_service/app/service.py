"""IP address management.

The pool is a single CIDR (e.g. `172.30.0.0/16`). It is sliced into equal-sized blocks
(default /24) and one block is allocated per lab. The first usable address of each block
is the gateway; `.10` is the microVM management IP.

Allocation is transactional and protected against races via row-level locks.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import IPAllocation


class IPAMError(RuntimeError):
    pass


class IPAMExhausted(IPAMError):
    pass


@dataclass(frozen=True)
class AllocationResult:
    subnet: str
    gateway: str
    vm_ip: str


class IPAMService:
    """Manages subnet allocation for labs."""

    def __init__(self, pool: str, prefix_len: int) -> None:
        self._pool = ipaddress.ip_network(pool, strict=False)
        self._prefix_len = prefix_len
        if prefix_len <= self._pool.prefixlen:
            raise ValueError("prefix_len must be longer than the pool prefix")
        self._subnets: list[ipaddress.IPv4Network] = list(self._pool.subnets(new_prefix=prefix_len))

    @classmethod
    def from_settings(cls) -> "IPAMService":
        s = get_settings()
        return cls(s.ipam_pool, s.ipam_prefix_len)

    def total_blocks(self) -> int:
        return len(self._subnets)

    def _gateway_for(self, subnet: ipaddress.IPv4Network) -> str:
        return str(subnet.network_address + 1)

    def _vm_ip_for(self, subnet: ipaddress.IPv4Network) -> str:
        return str(subnet.network_address + 10)

    async def allocate(self, db: AsyncSession, lab_id: str) -> AllocationResult:
        """Allocate the next free block to `lab_id`."""
        # Row-lock existing allocations so concurrent allocates serialise.
        await db.execute(select(IPAllocation).with_for_update())

        taken = await db.execute(
            select(IPAllocation.subnet).where(IPAllocation.released_at.is_(None))
        )
        taken_subnets: set[str] = {row[0] for row in taken.all()}

        for block in self._subnets:
            subnet = str(block)
            if subnet in taken_subnets:
                continue

            alloc = IPAllocation(
                lab_id=lab_id,
                subnet=subnet,
                gateway=self._gateway_for(block),
                vm_ip=self._vm_ip_for(block),
            )
            db.add(alloc)
            try:
                await db.flush()
            except IntegrityError:
                # raced with another allocator that took the same block — try next
                await db.rollback()
                continue

            return AllocationResult(
                subnet=subnet,
                gateway=self._gateway_for(block),
                vm_ip=self._vm_ip_for(block),
            )

        raise IPAMExhausted(f"no free /{self._prefix_len} blocks left in pool {self._pool}")

    async def hard_release(self, db: AsyncSession, lab_id: str) -> None:
        """Used during destruction — deletes the row outright. Idempotent."""
        await db.execute(delete(IPAllocation).where(IPAllocation.lab_id == lab_id))
        await db.flush()

    async def release(self, db: AsyncSession, lab_id: str) -> None:
        """Soft release — keeps the row, marks it inactive."""
        import datetime as _dt

        await db.execute(
            update(IPAllocation)
            .where(IPAllocation.lab_id == lab_id, IPAllocation.released_at.is_(None))
            .values(released_at=_dt.datetime.now(_dt.timezone.utc))
        )
        await db.flush()

    async def get(self, db: AsyncSession, lab_id: str) -> IPAllocation | None:
        result = await db.execute(
            select(IPAllocation).where(
                IPAllocation.lab_id == lab_id, IPAllocation.released_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def list_active(self, db: AsyncSession) -> list[IPAllocation]:
        result = await db.execute(
            select(IPAllocation).where(IPAllocation.released_at.is_(None))
        )
        return list(result.scalars().all())