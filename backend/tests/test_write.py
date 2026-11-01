import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fake_embedder import FakeEmbedder
from sqlalchemy import select

from app.agent import store
from app.agent.messages import UserMessage
from app.memory.extract import Proposal
from app.memory.reconcile import REASONS, Decision
from app.memory.search import search
from app.memory.write import (
    MemoryNotFound,
    WrongStatus,
    add_memory,
    apply_decisions,
    delete,
    supersede,
)
from app.models import Memory, MemoryEvent

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=2)


@pytest.fixture
def embedder():
    return FakeEmbedder()


async def vector(embedder, text):
    return (await embedder.embed([text]))[0]


async def owner_message(session):
    chat = await store.create_session(session)
    return await store.append(session, chat, UserMessage(text="I moved to Porto."))


async def make(session, embedder, content="The owner lives in Lisbon.", **change):
    fields = {"kind": "fact", "now": NOW, "reason": "the owner said it", **change}
    memory_id = await add_memory(
        session, content=content, embedding=await vector(embedder, content), **fields
    )
    await session.commit()
    return memory_id


async def row(session, memory_id) -> Memory:
    session.expire_all()
    return (await session.execute(select(Memory).where(Memory.id == memory_id))).scalar_one()


async def events(session, memory_id):
    found = await session.execute(
        select(MemoryEvent).where(MemoryEvent.memory_id == memory_id).order_by(MemoryEvent.id)
    )
    return [(e.event, e.reason, e.message_id, e.created_at) for e in found.scalars()]


# add ------------------------------------------------------------------------------------------


async def test_an_added_memory_is_an_active_owner_memory_with_its_provenance(session, embedder):
    message_id = await owner_message(session)
    until = NOW + timedelta(days=30)

    memory_id = await add_memory(
        session,
        kind="event",
        content="  Dentist \n on 3 March. ",
        embedding=await vector(embedder, "x"),
        now=NOW,
        reason="the owner said it",
        message_id=message_id,
        valid_until=until,
    )
    await session.commit()

    memory = await row(session, memory_id)
    assert (memory.kind, memory.content) == ("event", "Dentist on 3 March.")
    assert (memory.status, memory.source_kind) == ("active", "owner")
    assert memory.source_message_id == message_id
    assert (memory.created_at, memory.valid_until) == (NOW, until)
    assert memory.valid_from is None and memory.superseded_by is None
    assert memory.embedding is not None


async def test_an_added_memory_gets_a_created_event_with_the_reason_and_message(session, embedder):
    message_id = await owner_message(session)

    memory_id = await make(session, embedder, message_id=message_id)

    assert await events(session, memory_id) == [("created", "the owner said it", message_id, NOW)]


async def test_an_empty_memory_is_refused_and_writes_nothing(session, embedder):
    with pytest.raises(ValueError, match="empty"):
        await add_memory(
            session,
            kind="fact",
            content="  \n ",
            embedding=await vector(embedder, "x"),
            now=NOW,
            reason="r",
        )

    assert (await session.execute(select(Memory))).first() is None


# supersede ------------------------------------------------------------------------------------


async def replace(session, embedder, old_ids, content="The owner lives in Porto.", **change):
    fields = {"kind": "fact", "now": LATER, "reason": "the owner's new statement replaced it"}
    new_id = await supersede(
        session,
        old_ids,
        content=content,
        embedding=await vector(embedder, content),
        **{**fields, **change},
    )
    await session.commit()
    return new_id


async def test_a_replacement_adds_the_new_row_and_retires_the_old_one(session, embedder):
    old = await make(session, embedder)

    new = await replace(session, embedder, [old])

    retired = await row(session, old)
    assert (retired.status, retired.superseded_by) == ("superseded", new)
    current = await row(session, new)
    assert (current.status, current.content) == ("active", "The owner lives in Porto.")
    assert current.created_at == LATER


async def test_a_replacement_logs_created_for_the_new_row_and_superseded_for_the_old(
    session, embedder
):
    message_id = await owner_message(session)
    old = await make(session, embedder)

    new = await replace(session, embedder, [old], message_id=message_id)

    reason = "the owner's new statement replaced it"
    assert await events(session, new) == [("created", reason, message_id, LATER)]
    assert await events(session, old) == [
        ("created", "the owner said it", None, NOW),
        ("superseded", reason, message_id, LATER),
    ]


async def test_the_replaced_row_stops_holding_when_it_was_replaced(session, embedder):
    open_ended = await make(session, embedder, "fact one")
    future = await make(
        session, embedder, "event", kind="event", valid_until=LATER + timedelta(days=9)
    )
    past = await make(session, embedder, "old event", kind="event", valid_until=NOW)

    await replace(session, embedder, [open_ended, future, past])

    assert (await row(session, open_ended)).valid_until == LATER
    assert (await row(session, future)).valid_until == LATER
    assert (await row(session, past)).valid_until == NOW  # it had already ended


async def test_one_new_memory_can_replace_several(session, embedder):
    first = await make(session, embedder, "lives in Lisbon")
    second = await make(session, embedder, "address is in Alfama")

    new = await replace(session, embedder, [first, second, first])

    assert (await row(session, first)).superseded_by == new
    assert (await row(session, second)).superseded_by == new
    assert len(await events(session, first)) == 2


@pytest.mark.parametrize("status", ["superseded", "deleted"])
async def test_only_an_active_memory_can_be_replaced_and_nothing_is_written_otherwise(
    session, embedder, status
):
    live = await make(session, embedder, "live")
    other = await make(session, embedder, "other")
    memory = await row(session, other)
    memory.status = status
    await session.commit()

    with pytest.raises(WrongStatus, match=status):
        await supersede(
            session,
            [live, other],
            kind="fact",
            content="new",
            embedding=await vector(embedder, "new"),
            now=LATER,
            reason="r",
        )

    # No rollback first: a refused change must not have written anything, even unflushed
    assert (await row(session, live)).status == "active"
    assert len((await session.execute(select(Memory))).all()) == 2
    assert await events(session, live) == [("created", "the owner said it", None, NOW)]


async def test_replacing_an_unknown_memory_or_nothing_is_refused(session, embedder):
    kwargs = {
        "kind": "fact",
        "content": "x",
        "embedding": await vector(embedder, "x"),
        "now": LATER,
        "reason": "r",
    }

    with pytest.raises(MemoryNotFound):
        await supersede(session, [404], **kwargs)
    with pytest.raises(ValueError, match="nothing"):
        await supersede(session, [], **kwargs)


# delete ---------------------------------------------------------------------------------------


async def test_a_delete_marks_the_memory_and_logs_why(session, embedder):
    message_id = await owner_message(session)
    memory_id = await make(session, embedder)

    changed = await delete(
        session, [memory_id], now=LATER, reason=REASONS["delete"], message_id=message_id
    )
    await session.commit()

    assert changed == [memory_id]
    assert (await row(session, memory_id)).status == "deleted"
    assert (await events(session, memory_id))[-1] == (
        "deleted",
        REASONS["delete"],
        message_id,
        LATER,
    )


async def test_the_text_stays_on_file_after_a_delete(session, embedder):
    memory_id = await make(session, embedder, "The owner has a cat.")

    await delete(session, [memory_id], now=LATER, reason="r")
    await session.commit()

    memory = await row(session, memory_id)
    assert memory.content == "The owner has a cat." and memory.embedding is not None


async def test_deleting_a_deleted_memory_changes_and_logs_nothing(session, embedder):
    memory_id = await make(session, embedder)
    await delete(session, [memory_id], now=LATER, reason="r")
    await session.commit()

    again = await delete(session, [memory_id], now=LATER + timedelta(days=1), reason="r")
    await session.commit()

    assert again == []
    assert len(await events(session, memory_id)) == 2


async def test_a_replaced_memory_can_be_deleted_too(session, embedder):
    old = await make(session, embedder)
    await replace(session, embedder, [old])

    changed = await delete(session, [old], now=LATER, reason="r")
    await session.commit()

    assert changed == [old] and (await row(session, old)).status == "deleted"


async def test_deleting_several_memories_is_one_change_and_an_unknown_one_stops_it(
    session, embedder
):
    first = await make(session, embedder, "one")
    second = await make(session, embedder, "two")

    assert await delete(session, [first, second, first], now=LATER, reason="r") == [first, second]
    with pytest.raises(MemoryNotFound):
        await delete(session, [first, 404], now=LATER, reason="r")


# what recall sees -----------------------------------------------------------------------------


async def test_a_deleted_memory_is_not_recalled(session, embedder):
    memory_id = await make(session, embedder, "The owner has a cat called Miso.")

    async def recalled():
        found = await search(
            session, "The owner has a cat called Miso.", now=LATER, mode="vector", embedder=embedder
        )
        return [r.memory_id for r in found]

    assert await recalled() == [memory_id]
    await delete(session, [memory_id], now=LATER, reason="r")
    await session.commit()
    assert await recalled() == []


async def test_a_replaced_memory_leaves_recall_and_is_found_as_history(session, embedder):
    old = await make(session, embedder, "The owner lives in Lisbon.")
    new = await replace(session, embedder, [old], "The owner lives in Porto.")

    current = await search(session, "where", now=LATER, mode="vector", embedder=embedder)
    past = await search(session, "where", now=LATER, mode="vector", embedder=embedder, history=True)

    assert [r.memory_id for r in current] == [new]
    assert [r.memory_id for r in past] == [old]


# apply_decisions ------------------------------------------------------------------------------


def decision(action, content="The owner lives in Porto.", ids=(), kind="fact", until=None):
    proposal = Proposal("delete" if action == "delete" else "add", content, kind, until)
    return Decision(action, proposal, tuple(ids), REASONS.get(action, "r"))


async def apply(session, embedder, *decisions, message_id=None, now=LATER):
    return await apply_decisions(
        session, list(decisions), embedder=embedder, message_id=message_id, now=now
    )


async def test_decisions_are_applied_and_committed_together(session, embedder):
    message_id = await owner_message(session)
    replaced = await make(session, embedder, "The owner lives in Lisbon.")
    forgotten = await make(session, embedder, "The owner had knee surgery.")

    applied = await apply(
        session,
        embedder,
        decision("supersede", ids=[replaced]),
        decision("add", "The owner has a cat.", kind="fact"),
        decision("delete", "surgery", ids=[forgotten]),
        decision("noop", "The owner is vegetarian."),
        message_id=message_id,
    )

    assert (len(applied.added), applied.superseded, applied.deleted, applied.skipped) == (
        2,
        (replaced,),
        (forgotten,),
        0,
    )
    assert (await row(session, replaced)).status == "superseded"
    assert (await row(session, forgotten)).status == "deleted"
    contents = {m.content for m in (await session.execute(select(Memory))).scalars()}
    assert {"The owner lives in Porto.", "The owner has a cat."} <= contents
    assert {e[2] for e in await events(session, applied.added[0])} == {message_id}


async def test_all_new_texts_are_embedded_in_one_call(session, embedder):
    embedder.calls.clear()

    await apply(
        session,
        embedder,
        decision("add", "one"),
        decision("noop", "skipped"),
        decision("add", "two"),
    )

    assert embedder.calls == [["one", "two"]]


async def test_a_turn_with_nothing_to_write_embeds_nothing(session, embedder):
    embedder.calls.clear()

    applied = await apply(session, embedder, decision("noop"), decision("delete", ids=[]))

    assert applied.added == () and embedder.calls == []


async def test_the_proposals_end_date_and_kind_are_kept(session, embedder):
    until = NOW + timedelta(days=5)

    applied = await apply(session, embedder, decision("add", "Dentist.", kind="event", until=until))

    memory = await row(session, applied.added[0])
    assert (memory.kind, memory.valid_until, memory.created_at) == ("event", until, LATER)


async def test_a_failed_embedding_writes_nothing(session, embedder):
    class Broken:
        async def embed(self, texts):
            raise RuntimeError("no embeddings today")

    with pytest.raises(RuntimeError):
        await apply(session, Broken(), decision("add"))

    assert (await session.execute(select(Memory))).first() is None


async def test_a_decision_whose_memory_changed_meanwhile_is_skipped_and_the_rest_apply(
    session, embedder
):
    taken = await make(session, embedder, "The owner lives in Lisbon.")
    await delete(session, [taken], now=NOW, reason="the owner deleted it in the app")
    await session.commit()
    other = await make(session, embedder, "The owner has a dog.")

    applied = await apply(
        session,
        embedder,
        decision("supersede", "The owner lives in Porto.", ids=[taken]),
        decision("add", "The owner has a cat."),
        decision("delete", "dog", ids=[other]),
        decision("delete", "ghost", ids=[404]),
    )

    assert applied.skipped == 2 and applied.deleted == (other,) and len(applied.added) == 1
    assert (await row(session, taken)).status == "deleted"
    texts = {m.content for m in (await session.execute(select(Memory))).scalars()}
    assert "The owner lives in Porto." not in texts  # nothing half-written for the skipped one


async def test_a_replacement_of_several_memories_is_applied_as_one(session, embedder):
    first = await make(session, embedder, "lives in Lisbon")
    second = await make(session, embedder, "address in Alfama")

    applied = await apply(session, embedder, decision("supersede", ids=[first, second]))

    assert applied.superseded == (first, second) and len(applied.added) == 1
    assert (await row(session, first)).superseded_by == applied.added[0]


async def test_an_add_without_a_kind_is_a_programming_error_not_a_silent_default(session, embedder):
    with pytest.raises(ValueError, match="kind"):
        await apply(session, embedder, decision("add", kind=None))


# Two writers ----------------------------------------------------------------------------------


async def test_a_change_waits_for_a_concurrent_one_and_then_sees_what_it_did(
    session_factory, embedder
):
    async with session_factory() as first, session_factory() as second:
        memory_id = await make(first, embedder)
        await delete(first, [memory_id], now=LATER, reason="the owner deleted it in the app")
        # `first` has written but not committed, so it holds the row

        attempt = asyncio.create_task(
            supersede(
                second,
                [memory_id],
                kind="fact",
                content="The owner lives in Porto.",
                embedding=await vector(embedder, "x"),
                now=LATER,
                reason="the owner's new statement replaced it",
            )
        )
        await asyncio.sleep(0.3)
        assert not attempt.done()  # waiting for the lock, not racing past it

        await first.commit()
        with pytest.raises(WrongStatus, match="deleted"):
            await attempt
