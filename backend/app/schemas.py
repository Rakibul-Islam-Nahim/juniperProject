"""Pydantic request/response models."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.registry import REGISTRY as _LAB_TYPE_REGISTRY



class CreateLabRequest(BaseModel):
    lab_type: str
    cpu: int = Field(default=4, ge=1, le=64)
    memory: str = Field(default="8G")
    user_id: Optional[str] = Field(default=None)

    @field_validator("lab_type")
    @classmethod
    def _lab_type_known(cls, v: str) -> str:
        if v not in _LAB_TYPE_REGISTRY:
            raise ValueError(f"lab_type must be one of {sorted(_LAB_TYPE_REGISTRY)}")
        return v

    @field_validator("memory")
    @classmethod
    def _memory_format(cls, v: str) -> str:
        if not re.match(r"^\d+[MG]$", v):
            raise ValueError("memory must look like '4G' or '8192M'")
        return v


class LabResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    lab_id: str
    user_id: Optional[str] = None
    lab_type: str
    golden_image: Optional[str] = None
    status: str
    progress: int = 0
    cpu: int
    memory_mb: int
    subnet: Optional[str] = None
    gateway: Optional[str] = None
    vm_ip: Optional[str] = None
    vm_pid: Optional[int] = None
    tap_name: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    ready_at: Optional[datetime] = None
    terminated_at: Optional[datetime] = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[dict] = None


class ErrorResponse(BaseModel):
    error: ErrorBody


Memory = Annotated[str, Field(pattern=r"^\d+[MG]$")]
