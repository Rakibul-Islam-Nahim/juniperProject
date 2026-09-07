"""Concurrency and resource quota enforcement."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Lab
from app.state_machine import LabStatus


class QuotaExceeded(RuntimeError):
    def __init__(self, message: str, *, current: int, limit: int, kind: str):
        super().__init__(message)
        self.current = current
        self.limit = limit
        self.kind = kind


@dataclass
class UsageSnapshot:
    running_labs: int
    used_cpu: int
    used_mem_mb: int


# Statuses that consume capacity.
ACTIVE_STATUSES = {
    LabStatus.REQUESTED,
    LabStatus.CREATING,
    LabStatus.NETWORK_ALLOCATED,
    LabStatus.VM_STARTING,
    LabStatus.VM_READY,
    LabStatus.CONTAINERLAB_STARTING,
    LabStatus.DEVICES_BOOTING,
    LabStatus.LAB_READY,
}


class ResourceManager:
    def __init__(
        self,
        *,
        max_labs: int,
        cpu_quota: int,
        mem_quota_mb: int,
    ) -> None:
        self.max_labs = max_labs
        self.cpu_quota = cpu_quota
        self.mem_quota_mb = mem_quota_mb

    @classmethod
    def from_settings(cls) -> "ResourceManager":
        s = get_settings()
        return cls(
            max_labs=s.max_labs_per_host,
            cpu_quota=s.cpu_quota,
            mem_quota_mb=s.mem_quota_mb,
        )

    async def usage(self, db: AsyncSession) -> UsageSnapshot:
        result = await db.execute(
            select(func.count(Lab.id), func.coalesce(func.sum(Lab.cpu), 0), func.coalesce(func.sum(Lab.memory_mb), 0))
            .where(Lab.status.in_({s.value for s in ACTIVE_STATUSES}))
        )
        n, cpu, mem = result.one()
        return UsageSnapshot(running_labs=int(n), used_cpu=int(cpu), used_mem_mb=int(mem))

    async def can_admit(self, db: AsyncSession, *, cpu: int, memory_mb: int) -> None:
        snap = await self.usage(db)
        if snap.running_labs + 1 > self.max_labs:
            raise QuotaExceeded(
                f"host has {snap.running_labs} labs already, limit is {self.max_labs}",
                current=snap.running_labs,
                limit=self.max_labs,
                kind="labs",
            )
        if snap.used_cpu + cpu > self.cpu_quota:
            raise QuotaExceeded(
                f"would exceed cpu quota ({snap.used_cpu}+{cpu} > {self.cpu_quota})",
                current=snap.used_cpu + cpu,
                limit=self.cpu_quota,
                kind="cpu",
            )
        if snap.used_mem_mb + memory_mb > self.mem_quota_mb:
            raise QuotaExceeded(
                f"would exceed memory quota ({snap.used_mem_mb}+{memory_mb} > {self.mem_quota_mb}) MiB",
                current=snap.used_mem_mb + memory_mb,
                limit=self.mem_quota_mb,
                kind="memory_mb",
            )

    def filter_active(self, labs: Iterable[Lab]) -> list[Lab]:
        return [l for l in labs if LabStatus(l.status) in ACTIVE_STATUSES]