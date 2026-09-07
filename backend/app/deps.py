"""FastAPI dependencies."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import SessionLocal


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def db_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


DBSession = Annotated[AsyncSession, Depends(db_session)]
