from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_owner
from app.db import get_session
from app.models import Memory

router = APIRouter(prefix="/memories", tags=["memories"], dependencies=[Depends(current_owner)])


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    content: str
    status: str
    source_kind: str
    superseded_by: int | None
    valid_from: datetime | None
    valid_until: datetime | None
    created_at: datetime
    last_used_at: datetime | None


@router.get("")
async def list_memories(
    db: Annotated[AsyncSession, Depends(get_session)],
    status: Literal["active", "superseded", "deleted"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MemoryOut]:
    """Read-only, newest first. Every status is listed unless one is asked for; editing is phase 5."""
    query = select(Memory).order_by(Memory.created_at.desc(), Memory.id.desc())
    if status:
        query = query.where(Memory.status == status)
    found = await db.execute(query.limit(limit).offset(offset))
    return [MemoryOut.model_validate(memory) for memory in found.scalars()]
