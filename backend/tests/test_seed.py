from datetime import UTC, datetime

import pytest
import yaml
from fake_embedder import FakeEmbedder
from sqlalchemy import func, select, text, update

from app.memory.seed import InvalidSeed, load_seed, reset, restore, seed, snapshot
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


# Snapshot and restore, for an eval whose cases write ------------------------------------------


def plain(saved):
    """A snapshot as values that compare, with the embeddings as lists."""
    return (
        [{**row, "embedding": list(row["embedding"])} for row in saved.memories],
        list(saved.events),
    )


async def seeded(session):
    ids = await seed(session, FakeEmbedder(), load_seed())
    return ids, await snapshot(session)


async def test_a_snapshot_holds_every_row_with_its_embedding_and_log(session):
    ids, saved = await seeded(session)

    assert len(saved.memories) == 42 and len(saved.events) > 42
    assert {row["id"] for row in saved.memories} == set(ids.values())
    assert all(row["embedding"] is not None and "tsv" not in row for row in saved.memories)


async def test_restoring_an_untouched_table_changes_nothing(session):
    _, saved = await seeded(session)

    await restore(session, saved)

    assert plain(await snapshot(session)) == plain(saved)


async def test_what_was_added_changed_or_logged_since_is_undone(session):
    ids, saved = await seeded(session)
    live = ids["home-city"]
    session.add(Memory(kind="fact", content="a memory the agent wrote", status="active"))
    session.add(MemoryEvent(memory_id=live, event="deleted", reason="a later change"))
    await session.execute(
        update(Memory)
        .where(Memory.id == live)
        .values(
            status="deleted", last_used_at=datetime(2026, 10, 28, tzinfo=UTC), content="changed"
        )
    )
    await session.commit()

    await restore(session, saved)

    assert plain(await snapshot(session)) == plain(saved)
    assert (await session.execute(select(func.count()).select_from(Memory))).scalar_one() == 42


async def test_every_memory_keeps_its_id_so_a_key_still_names_it(session):
    ids, saved = await seeded(session)
    await session.execute(text("TRUNCATE memory_events, memories RESTART IDENTITY CASCADE"))
    await session.commit()

    await restore(session, saved)

    rows = (await session.execute(select(Memory.id, Memory.content))).all()
    id_of = {content: memory_id for memory_id, content in rows}
    assert {m.key: id_of[m.content] for m in load_seed()} == ids


async def test_replaced_memories_still_point_at_their_replacement(session):
    _, saved = await seeded(session)
    before = {row["id"]: row["superseded_by"] for row in saved.memories if row["superseded_by"]}
    assert before  # the seed has replaced memories

    await restore(session, saved)

    rows = (await session.execute(select(Memory.id, Memory.superseded_by))).all()
    assert {i: link for i, link in rows if link} == before


async def test_the_next_memory_gets_an_id_after_the_largest_and_does_not_collide(session):
    _, saved = await seeded(session)
    await restore(session, saved)

    session.add_all(
        [
            Memory(kind="fact", content="new one"),
            MemoryEvent(memory_id=1, event="created", reason="r"),
        ]
    )
    await session.commit()

    newest = (await session.execute(select(func.max(Memory.id)))).scalar_one()
    newest_event = (await session.execute(select(func.max(MemoryEvent.id)))).scalar_one()
    assert newest == max(row["id"] for row in saved.memories) + 1
    assert newest_event == max(row["id"] for row in saved.events) + 1


async def test_restoring_twice_is_the_same_as_once(session):
    _, saved = await seeded(session)

    await restore(session, saved)
    await restore(session, saved)

    assert plain(await snapshot(session)) == plain(saved)


async def test_the_search_index_is_rebuilt_with_the_rows(session):
    _, saved = await seeded(session)
    await restore(session, saved)

    missing = (
        await session.execute(select(func.count()).select_from(Memory).where(Memory.tsv.is_(None)))
    ).scalar_one()
    assert missing == 0


async def test_an_empty_snapshot_empties_the_tables_and_an_empty_table_can_be_restored(session):
    empty = await snapshot(session)
    await seed(session, FakeEmbedder(), load_seed())

    await restore(session, empty)

    assert (await session.execute(select(func.count()).select_from(Memory))).scalar_one() == 0
    assert (await session.execute(select(func.count()).select_from(MemoryEvent))).scalar_one() == 0
    _, saved = await seeded(session)
    await reset(session)
    await restore(session, saved)
    assert plain(await snapshot(session)) == plain(saved)


async def test_a_restore_is_committed_so_another_connection_sees_it(session, session_factory):
    _, saved = await seeded(session)
    session.add(Memory(kind="fact", content="written after the snapshot"))
    await session.commit()

    await restore(session, saved)

    async with session_factory() as other:
        count = (await other.execute(select(func.count()).select_from(Memory))).scalar_one()
    assert count == 42


async def test_last_used_is_put_back_as_it_was_in_the_table(session):
    ids, _ = await seeded(session)
    then = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
    target = ids["home-city"]
    await session.execute(update(Memory).where(Memory.id == target).values(last_used_at=then))
    await session.commit()
    saved = await snapshot(session)
    await session.execute(update(Memory).where(Memory.id == target).values(last_used_at=None))
    await session.commit()

    await restore(session, saved)

    got = (
        await session.execute(select(Memory.last_used_at).where(Memory.id == target))
    ).scalar_one()
    assert got == then
