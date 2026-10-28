"""initial schema

Revision ID: 0001
Revises:
"""

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'tool')", name="chat_messages_role_check"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "chat_messages_session_id_id_idx", "chat_messages", ["session_id", "id"], unique=False
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=False,
        ),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.Text(), server_default="owner", nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('fact', 'preference', 'event', 'episode')", name="memories_kind_check"
        ),
        sa.CheckConstraint(
            "source_kind IN ('owner', 'external')", name="memories_source_kind_check"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'deleted')", name="memories_status_check"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["chat_messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["memories.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "memories_embedding_idx",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("memories_status_idx", "memories", ["status"], unique=False)
    op.create_index("memories_tsv_idx", "memories", ["tsv"], unique=False, postgresql_using="gin")
    op.create_table(
        "tool_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confirmation_message_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('done', 'refused', 'error', 'unknown')", name="tool_audit_outcome_check"
        ),
        sa.ForeignKeyConstraint(
            ["confirmation_message_id"],
            ["chat_messages.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "memory_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event IN ('created', 'superseded', 'deleted', 'restored')",
            name="memory_events_event_check",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.id"],
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("memory_events_memory_id_idx", "memory_events", ["memory_id"], unique=False)


def downgrade() -> None:
    op.drop_index("memory_events_memory_id_idx", table_name="memory_events")
    op.drop_table("memory_events")
    op.drop_table("tool_audit")
    op.drop_index("memories_tsv_idx", table_name="memories", postgresql_using="gin")
    op.drop_index("memories_status_idx", table_name="memories")
    op.drop_index(
        "memories_embedding_idx",
        table_name="memories",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("memories")
    op.drop_index("chat_messages_session_id_id_idx", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("idempotency_keys")
    op.drop_table("chat_sessions")
