"""ORM models for the six tables. Alembic migrations are generated from these."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 1536


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# Conversations ---------------------------------------------------------
# There is one owner, so a session has no owner column.


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    created_at: Mapped[datetime] = _created_at()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="chat_messages_role_check"),
        Index("chat_messages_session_id_id_idx", "session_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[Any] = mapped_column(JSONB)  # tool calls and results replay exactly
    created_at: Mapped[datetime] = _created_at()


# Memory ----------------------------------------------------------------


class Memory(Base):
    """One thing the agent remembers. A change adds a row; nothing is overwritten."""

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('fact', 'preference', 'event', 'episode')", name="memories_kind_check"
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'deleted')", name="memories_status_check"
        ),
        # Text from an email or event can be stored, but never as an owner-level fact
        CheckConstraint("source_kind IN ('owner', 'external')", name="memories_source_kind_check"),
        Index("memories_tsv_idx", "tsv", postgresql_using="gin"),
        Index(
            "memories_embedding_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("memories_status_idx", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"))
    source_kind: Mapped[str] = mapped_column(Text, server_default="owner")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("memories.id"))
    # When the fact holds: an event that already happened has a valid_until in the past
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryEvent(Base):
    """Append-only log of every write, update and deletion, so any change can be explained."""

    __tablename__ = "memory_events"
    __table_args__ = (
        CheckConstraint(
            "event IN ('created', 'superseded', 'deleted', 'restored')",
            name="memory_events_event_check",
        ),
        Index("memory_events_memory_id_idx", "memory_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    memory_id: Mapped[int] = mapped_column(ForeignKey("memories.id"))
    event: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"))
    created_at: Mapped[datetime] = _created_at()


# Tool calls (used from phase 6; created now so the schema does not change under data) -------


class IdempotencyKey(Base):
    """A write call that has already run, so the same call sent again is answered, not repeated."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    tool: Mapped[str] = mapped_column(Text)
    # A hash of the tool and its arguments: the same key must mean the same request
    fingerprint: Mapped[str] = mapped_column(Text)
    output: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class ToolAudit(Base):
    """Each outside action, with the owner message that confirmed it."""

    __tablename__ = "tool_audit"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('done', 'refused', 'error', 'unknown')", name="tool_audit_outcome_check"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tool: Mapped[str] = mapped_column(Text)
    arguments: Mapped[Any] = mapped_column(JSONB)
    confirmation_message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"))
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
