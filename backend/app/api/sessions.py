import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import store
from app.agent.loop import AgentLoop, TurnRefused
from app.agent.messages import AssistantMessage, UserMessage
from app.api.deps import current_owner, get_agent
from app.api.sse import frames, run_in_background
from app.db import get_session

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(current_owner)])

# A refusal is answered before any stream opens, with its stable code in the body
REFUSAL_STATUS = {
    "session_not_found": 404,
    "empty_message": 422,
    "message_too_long": 413,
    "chat_full": 409,
    "turn_in_progress": 409,
}


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    message_count: int
    preview: str | None


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


def _out(summary: store.SessionSummary) -> SessionOut:
    return SessionOut(
        id=summary.id,
        created_at=summary.created_at,
        message_count=summary.message_count,
        preview=summary.preview,
    )


@router.post("", status_code=201)
async def create(db: Annotated[AsyncSession, Depends(get_session)]) -> SessionOut:
    session_id = await store.create_session(db)
    summary = await store.get_summary(db, session_id)
    assert summary is not None
    return _out(summary)


@router.get("")
async def list_all(
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SessionOut]:
    return [_out(summary) for summary in await store.list_sessions(db, limit)]


@router.get("/{session_id}/messages")
async def messages(
    session_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_session)]
) -> list[MessageOut]:
    """What the owner sees: their messages and the agent's answers. Tool calls and results stay out."""
    if not await store.session_exists(db, session_id):
        raise HTTPException(404, {"code": "session_not_found", "message": "no such chat"})
    try:
        rows = await store.load_rows(db, session_id)
    except ValueError:
        raise HTTPException(
            500, {"code": "history_unreadable", "message": "a stored message cannot be read"}
        ) from None
    return [
        MessageOut(
            id=row.id, role=row.message.role, text=row.message.text, created_at=row.created_at
        )
        for row in rows
        if isinstance(row.message, UserMessage | AssistantMessage) and row.message.text
    ]


@router.post("/{session_id}/messages")
async def send(
    session_id: uuid.UUID,
    body: SendRequest,
    request: Request,
    agent: Annotated[AgentLoop, Depends(get_agent)],
) -> StreamingResponse:
    """Takes the message, then streams the turn as events: memory, text (many), done or error."""
    try:
        turn = await agent.start(session_id, body.text)
    except TurnRefused as refusal:
        raise HTTPException(
            REFUSAL_STATUS[refusal.code], {"code": refusal.code, "message": str(refusal)}
        ) from None
    queue = run_in_background(turn, request.app.state.turns)
    return StreamingResponse(
        frames(queue),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
