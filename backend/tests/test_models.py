import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import EMBEDDING_DIMENSIONS, Base, ChatMessage, ChatSession, Memory, MemoryEvent


def test_all_six_tables_are_defined():
    assert set(Base.metadata.tables) == {
        "memories",
        "memory_events",
        "chat_sessions",
        "chat_messages",
        "idempotency_keys",
        "tool_audit",
    }


def test_memory_columns_match_the_embedding_model():
    assert Memory.__table__.c.embedding.type.dim == EMBEDDING_DIMENSIONS == 1536


def test_memory_text_search_column_is_generated():
    assert Memory.__table__.c.tsv.computed is not None


async def test_the_migration_builds_every_model_table(session):
    rows = await session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )

    assert set(Base.metadata.tables) <= {name for (name,) in rows}


async def test_the_vector_extension_is_installed(session):
    rows = await session.execute(text("SELECT extname FROM pg_extension"))

    assert "vector" in {name for (name,) in rows}


async def test_a_new_memory_is_an_active_owner_memory_with_search_text(session):
    session.add(Memory(kind="fact", content="The owner lives in Lisbon"))
    await session.commit()

    memory = (await session.execute(select(Memory))).scalar_one()
    assert (memory.status, memory.source_kind) == ("active", "owner")
    found = await session.execute(
        text("SELECT 1 FROM memories WHERE tsv @@ plainto_tsquery('english', 'lisbon')")
    )
    assert found.scalar() == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "rumour"},
        {"status": "archived"},
        {"source_kind": "model"},
    ],
)
async def test_a_memory_with_an_unknown_value_is_refused(session, fields):
    session.add(Memory(**{"kind": "fact", "content": "x"} | fields))

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_a_memory_can_point_to_the_one_that_replaced_it(session):
    old = Memory(kind="fact", content="Lives in Lisbon", status="superseded")
    new = Memory(kind="fact", content="Lives in Porto")
    session.add_all([old, new])
    await session.flush()
    old.superseded_by = new.id
    session.add(MemoryEvent(memory_id=old.id, event="superseded", reason="moved"))
    await session.commit()

    assert old.superseded_by == new.id


async def test_a_message_belongs_to_a_session_and_keeps_its_json(session):
    chat = ChatSession()
    session.add(chat)
    await session.flush()
    session.add(ChatMessage(session_id=chat.id, role="user", content={"text": "hi"}))
    await session.commit()

    message = (await session.execute(select(ChatMessage))).scalar_one()
    assert message.content == {"text": "hi"}


async def test_a_message_with_an_unknown_role_is_refused(session):
    chat = ChatSession()
    session.add(chat)
    await session.flush()
    session.add(ChatMessage(session_id=chat.id, role="system", content={}))

    with pytest.raises(IntegrityError):
        await session.commit()
