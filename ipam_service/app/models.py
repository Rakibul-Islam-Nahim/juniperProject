"""ORM model owned by the IPAM service.

The Backend Agent no longer imports or reads this table — it goes through the
HTTP API instead.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class IPAllocation(Base):
    __tablename__ = "ip_allocations"

    lab_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subnet: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    gateway: Mapped[str] = mapped_column(String(64), nullable=False)
    vm_ip: Mapped[str] = mapped_column(String(64), nullable=False)

    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)