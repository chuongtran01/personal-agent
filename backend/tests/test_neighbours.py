from datetime import UTC, datetime, timedelta

import pytest
from fake_embedder import FakeEmbedder

from app.memory.neighbours import DbNeighbours
from app.models import Memory

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


async def store(session_factory, embedder, *rows):
    """Memories written straight to the table; each row is (content, changes to the defaults)."""
    vectors = await embedder.embed([content for content, _ in rows])
    async with session_factory() as db:
        ids = []
        for (content, change), vector in zip(rows, vectors, strict=True):
            memory = Memory(**{"kind": "fact", "content": content, "embedding": vector, **change})
            db.add(memory)
            await db.flush()
            ids.append(memory.id)
        await db.commit()
    return ids


@pytest.fixture
def embedder():
    return FakeEmbedder()


async def test_the_nearest_memory_comes_first_with_its_similarity(session_factory, embedder):
    same, other = await store(
        session_factory, embedder, ("The owner lives in Lisbon.", {}), ("The owner has a dog.", {})
    )

    found = await DbNeighbours(session_factory, embedder).nearest("The owner lives in Lisbon.", 5)

    assert [c.id for c in found] == [same, other]
    assert found[0].similarity == pytest.approx(1.0)
    assert found[0].similarity > found[1].similarity


async def test_a_candidate_carries_what_the_prompt_shows(session_factory, embedder):
    (memory_id,) = await store(
        session_factory,
        embedder,
        ("A members' night.", {"kind": "event", "source_kind": "external"}),
    )

    (found,) = await DbNeighbours(session_factory, embedder).nearest("anything", 5)

    assert (found.id, found.kind, found.content, found.source_kind) == (
        memory_id,
        "event",
        "A members' night.",
        "external",
    )
    assert found.created_at.tzinfo is not None


async def test_replaced_and_deleted_memories_are_never_candidates(session_factory, embedder):
    live, _, _ = await store(
        session_factory,
        embedder,
        ("live", {}),
        ("replaced", {"status": "superseded"}),
        ("deleted", {"status": "deleted"}),
    )

    found = await DbNeighbours(session_factory, embedder).nearest("live", 10)

    assert [c.id for c in found] == [live]


async def test_ended_upcoming_and_external_memories_are_candidates(session_factory, embedder):
    ended, upcoming, external = await store(
        session_factory,
        embedder,
        ("ended event", {"kind": "event", "valid_until": NOW - timedelta(days=30)}),
        ("starts later", {"valid_from": NOW + timedelta(days=60)}),
        ("from an email", {"source_kind": "external"}),
    )

    found = await DbNeighbours(session_factory, embedder).nearest("x", 10)

    assert {c.id for c in found} == {ended, upcoming, external}


async def test_a_memory_with_no_embedding_is_skipped(session_factory, embedder):
    async with session_factory() as db:
        db.add(Memory(kind="fact", content="no vector yet"))
        await db.commit()
    (with_vector,) = await store(session_factory, embedder, ("has a vector", {}))

    found = await DbNeighbours(session_factory, embedder).nearest("x", 10)

    assert [c.id for c in found] == [with_vector]


async def test_the_limit_is_respected_and_the_text_is_embedded_once(session_factory, embedder):
    await store(session_factory, embedder, *[(f"memory {n}", {}) for n in range(6)])
    embedder.calls.clear()

    found = await DbNeighbours(session_factory, embedder).nearest("memory 3", 3)

    assert len(found) == 3 and found[0].content == "memory 3"
    assert embedder.calls == [["memory 3"]]


async def test_nothing_stored_is_no_candidates(session_factory, embedder):
    assert await DbNeighbours(session_factory, embedder).nearest("x", 5) == []


async def test_a_request_to_forget_also_sees_replaced_memories_marked_as_such(
    session_factory, embedder
):
    live, replaced, _ = await store(
        session_factory,
        embedder,
        ("live", {}),
        ("replaced", {"status": "superseded"}),
        ("deleted", {"status": "deleted"}),
    )

    found = await DbNeighbours(session_factory, embedder).nearest("x", 10, history=True)

    assert {c.id: c.status for c in found} == {live: "active", replaced: "superseded"}


async def test_without_history_every_candidate_is_active(session_factory, embedder):
    await store(session_factory, embedder, ("live", {}), ("replaced", {"status": "superseded"}))

    found = await DbNeighbours(session_factory, embedder).nearest("x", 10)

    assert {c.status for c in found} == {"active"}
