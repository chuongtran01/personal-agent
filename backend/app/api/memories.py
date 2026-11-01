from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_owner, get_now
from app.db import get_session
from app.memory.write import MemoryNotFound, delete
from app.models import Memory, MemoryEvent

# A fixed phrase for memory_events, as in the write path: it never quotes a memory
DELETED = "deleted by the owner in the app"

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


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event: str
    reason: str
    message_id: int | None
    created_at: datetime


def refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def not_found(memory_id: int) -> HTTPException:
    return refuse(404, "memory_not_found", f"no memory {memory_id}")


@router.get("")
async def list_memories(
    db: Annotated[AsyncSession, Depends(get_session)],
    status: Literal["active", "superseded", "deleted"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MemoryOut]:
    """Newest first. Every status is listed unless one is asked for."""
    query = select(Memory).order_by(Memory.created_at.desc(), Memory.id.desc())
    if status:
        query = query.where(Memory.status == status)
    found = await db.execute(query.limit(limit).offset(offset))
    return [MemoryOut.model_validate(memory) for memory in found.scalars()]


async def _get(db: AsyncSession, memory_id: int) -> Memory:
    memory = await db.get(Memory, memory_id)
    if memory is None:
        raise not_found(memory_id)
    return memory


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: int,
    db: Annotated[AsyncSession, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
) -> Response:
    """Mark a memory deleted, whatever it was: it is never recalled again. The text stays on file,
    listed under Deleted (docs/PHASE5.md says why, and what erasing for good would add). Deleting
    what is already deleted is fine."""
    try:
        await delete(db, [memory_id], now=now, reason=DELETED)
    except MemoryNotFound:
        raise not_found(memory_id) from None
    await db.commit()
    return Response(status_code=204)


@router.get("/{memory_id}/events")
async def memory_events(
    memory_id: int, db: Annotated[AsyncSession, Depends(get_session)]
) -> list[EventOut]:
    """Everything that happened to one memory, oldest first, with the reason for each."""
    await _get(db, memory_id)
    found = await db.execute(
        select(MemoryEvent).where(MemoryEvent.memory_id == memory_id).order_by(MemoryEvent.id)
    )
    return [EventOut.model_validate(event) for event in found.scalars()]
