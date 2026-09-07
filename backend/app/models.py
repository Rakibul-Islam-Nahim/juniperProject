"""ORM models."""
from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lab_type: Mapped[str] = mapped_column(String(64), nullable=False)
    golden_image: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    cpu: Mapped[int] = mapped_column(Integer, nullable=False, server_default="4")
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False, server_default="8192")
    subnet: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    gateway: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vm_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vm_pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vm_api_socket: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    tap_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    ip_allocation: Mapped[Optional["IPAllocation"]] = relationship(
        back_populates="lab", uselist=False, cascade="all, delete-orphan"
    )
    events: Mapped[list["LabEvent"]] = relationship(
        back_populates="lab", cascade="all, delete-orphan", order_by="LabEvent.id"
    )


class IPAllocation(Base):
    __tablename__ = "ip_allocations"

    lab_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("labs.id", ondelete="CASCADE"), primary_key=True
    )
    subnet: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    gateway: Mapped[str] = mapped_column(String(64), nullable=False)
    vm_ip: Mapped[str] = mapped_column(String(64), nullable=False)

    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    lab: Mapped[Lab] = relationship(back_populates="ip_allocation")


class LabEvent(Base):
    __tablename__ = "lab_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lab_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("labs.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    to_status: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lab: Mapped[Lab] = relationship(back_populates="events")
