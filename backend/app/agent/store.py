"""Reading and writing a conversation. Each call is one short transaction on the session it is given."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.messages import AssistantMessage, ToolResultMessage, UserMessage, from_row, to_row
from app.models import ChatMessage, ChatSession

StoredMessage = UserMessage | AssistantMessage | ToolResultMessage


async def create_session(db: AsyncSession) -> uuid.UUID:
    session = ChatSession()
    db.add(session)
    await db.commit()
    return session.id


async def session_exists(db: AsyncSession, session_id: uuid.UUID) -> bool:
    found = await db.execute(select(ChatSession.id).where(ChatSession.id == session_id))
    return found.scalar_one_or_none() is not None


async def count_messages(db: AsyncSession, session_id: uuid.UUID) -> int:
    count = await db.execute(
        select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id)
    )
    return count.scalar_one()


async def append(db: AsyncSession, session_id: uuid.UUID, message: StoredMessage) -> int:
    """Store a message and return its id."""
    role, content = to_row(message)
    row = ChatMessage(session_id=session_id, role=role, content=content)
    db.add(row)
    await db.commit()
    return row.id


async def load(db: AsyncSession, session_id: uuid.UUID) -> list[StoredMessage]:
    """The conversation in order. Raises ValueError if a stored row does not fit its role."""
    rows = await db.execute(
        select(ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
    )
    return [from_row(role, content) for role, content in rows.all()]


@dataclass(frozen=True)
class SessionSummary:
    id: uuid.UUID
    created_at: datetime
    message_count: int
    preview: str | None  # the start of the first owner message


@dataclass(frozen=True)
class StoredRow:
    id: int
    created_at: datetime
    message: StoredMessage


PREVIEW_CHARS = 80


def _summaries():
    count = (
        select(func.count(ChatMessage.id))
        .where(ChatMessage.session_id == ChatSession.id)
        .scalar_subquery()
    )
    first = (
        select(ChatMessage.content["text"].astext)
        .where(ChatMessage.session_id == ChatSession.id, ChatMessage.role == "user")
        .order_by(ChatMessage.id)
        .limit(1)
        .scalar_subquery()
    )
    return select(ChatSession.id, ChatSession.created_at, count, first)


def _summary(row) -> SessionSummary:
    session_id, created_at, count, first = row
    preview = " ".join(first.split())[:PREVIEW_CHARS] if first else None
    return SessionSummary(session_id, created_at, count, preview)


async def get_summary(db: AsyncSession, session_id: uuid.UUID) -> SessionSummary | None:
    found = await db.execute(_summaries().where(ChatSession.id == session_id))
    row = found.one_or_none()
    return _summary(row) if row else None


async def list_sessions(db: AsyncSession, limit: int) -> list[SessionSummary]:
    """Chats, newest first."""
    found = await db.execute(
        _summaries().order_by(ChatSession.created_at.desc(), ChatSession.id.desc()).limit(limit)
    )
    return [_summary(row) for row in found.all()]


async def load_rows(db: AsyncSession, session_id: uuid.UUID) -> list[StoredRow]:
    """The conversation with ids and times, for showing it. Raises ValueError as ``load`` does."""
    rows = await db.execute(
        select(ChatMessage.id, ChatMessage.created_at, ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
    )
    return [
        StoredRow(row_id, created_at, from_row(role, content))
        for row_id, created_at, role, content in rows.all()
    ]
