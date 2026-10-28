import pytest
import yaml
from fake_embedder import FakeEmbedder
from sqlalchemy import func, select

from app.memory.seed import InvalidSeed, load_seed, reset, seed
from app.models import Memory, MemoryEvent


def entry(key, **fields):
    return {
        "key": key,
        "kind": "fact",
        "content": f"content of {key}",
        "created_at": "2026-01-01T00:00:00Z",
    } | fields


def write(tmp_path, rows):
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump({"memories": rows}))
    return path


def test_the_committed_seed_file_loads():
    memories = load_seed()

    assert len(memories) == 42
    assert {m.status for m in memories} == {"active", "superseded", "deleted"}
    assert {m.source_kind for m in memories} == {"owner", "external"}


def test_times_are_read_as_utc():
    assert all(m.created_at.tzinfo is not None for m in load_seed())


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([entry("a"), entry("a", content="other")], "duplicate keys"),
        ([entry("a"), entry("b", content="content of a")], "duplicate content"),
        ([entry("a", status="superseded")], "only a superseded memory"),
        ([entry("a", superseded_by="b"), entry("b")], "only a superseded memory"),
        ([entry("a", status="superseded", superseded_by="nope")], "missing or inactive"),
        (
            [entry("a", status="superseded", superseded_by="b"), entry("b", status="deleted")],
            "missing or inactive",
        ),
        ([entry("a", kind="rumour")], "kind"),
        ([entry("a", surprise=1)], "surprise"),
    ],
)
def test_a_bad_seed_file_is_refused(tmp_path, rows, message):
    with pytest.raises(InvalidSeed, match=message):
        load_seed(write(tmp_path, rows))


async def count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_seeding_stores_every_memory_embedded_with_a_log_entry(session):
    memories = load_seed()

    ids = await seed(session, FakeEmbedder(), memories)

    assert len(ids) == len(set(ids.values())) == 42
    assert await count(session, Memory) == 42
    unembedded = await session.execute(select(Memory).where(Memory.embedding.is_(None)))
    assert unembedded.first() is None
    created = await session.execute(select(MemoryEvent).where(MemoryEvent.event == "created"))
    assert len(created.all()) == 42


async def test_superseded_and_deleted_memories_are_logged_and_linked(session):
    ids = await seed(session, FakeEmbedder(), load_seed())

    old = await session.get(Memory, ids["home-city-old"])
    assert (old.status, old.superseded_by) == ("superseded", ids["home-city"])
    events = await session.execute(
        select(MemoryEvent.event)
        .where(MemoryEvent.memory_id == ids["surgery"])
        .order_by(MemoryEvent.id)
    )
    assert [e for (e,) in events] == ["created", "deleted"]


async def test_a_second_run_writes_and_embeds_nothing(session):
    memories = load_seed()
    first = await seed(session, FakeEmbedder(), memories)
    embedder = FakeEmbedder()

    second = await seed(session, embedder, memories)

    assert second == first
    assert embedder.calls == []
    assert await count(session, Memory) == 42


async def test_only_new_memories_are_added_and_embedded(session):
    memories = load_seed()
    await seed(session, FakeEmbedder(), memories[:40])
    embedder = FakeEmbedder()

    await seed(session, embedder, memories)

    assert [len(batch) for batch in embedder.calls] == [2]
    assert await count(session, Memory) == 42


async def test_reset_empties_the_memory_tables(session):
    await seed(session, FakeEmbedder(), load_seed())

    await reset(session)

    assert await count(session, Memory) == await count(session, MemoryEvent) == 0
