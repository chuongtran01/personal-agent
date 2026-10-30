"""Reading and writing a conversation. Each call is one short transaction on the session it is given."""

import uuid

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
