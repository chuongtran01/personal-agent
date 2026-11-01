import asyncio
import logging
from datetime import UTC, datetime

import pytest
from scripted_llm import ScriptedStructured, answer
from sqlalchemy import select
from word_embedder import WordEmbedder

import app.memory.writer as writer_module
from app.agent import store
from app.agent.llm import LLMError, Usage
from app.agent.messages import UserMessage
from app.memory.extract import ContextMessage
from app.memory.reconcile import REASONS
from app.memory.write import add_memory
from app.memory.writer import MemoryWriter, WriteJob
from app.models import Memory, MemoryEvent

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
TZ = "Europe/Lisbon"


def proposals(*items, usage=None):
    return answer({"proposals": list(items)}, usage)


def add(content, kind="fact", until=None):
    return {"op": "add", "kind": kind, "content": content, "valid_until": until}


def forget(content):
    return {"op": "delete", "kind": None, "content": content, "valid_until": None}


def decide(decision, *ids, usage=None):
    return answer({"decision": decision, "memory_ids": list(ids)}, usage)


def forgets(*ids):
    return answer({"memory_ids": list(ids)})


@pytest.fixture
def embedder():
    return WordEmbedder()


def make_writer(session_factory, embedder, *answers):
    llm = ScriptedStructured(*answers)
    return MemoryWriter(session_factory, llm, embedder, timezone=TZ), llm


async def owner_message(session_factory, text="I moved to Porto."):
    async with session_factory() as db:
        chat = await store.create_session(db)
        return await store.append(db, chat, UserMessage(text=text))


async def job(session_factory, text="I moved to Porto.", **change):
    fields = {"now": NOW, "reply": "Noted.", **change}
    return WriteJob(text, await owner_message(session_factory, text), **fields)


async def stored(session_factory, embedder, content, **change):
    async with session_factory() as db:
        fields = {"kind": "fact", "now": NOW, "reason": "the owner said it", **change}
        vector = (await embedder.embed([content]))[0]
        memory_id = await add_memory(db, content=content, embedding=vector, **fields)
        await db.commit()
    return memory_id


async def memories(session_factory):
    async with session_factory() as db:
        found = await db.execute(select(Memory).order_by(Memory.id))
        return [(m.id, m.content, m.status) for m in found.scalars()]


async def events(session_factory, memory_id):
    async with session_factory() as db:
        found = await db.execute(
            select(MemoryEvent).where(MemoryEvent.memory_id == memory_id).order_by(MemoryEvent.id)
        )
        return [(e.event, e.reason, e.message_id) for e in found.scalars()]


# One job, start to finish ----------------------------------------------------------------------


async def test_a_stated_fact_is_stored_with_where_it_came_from(session_factory, embedder):
    work = await job(session_factory, "I'm allergic to penicillin.")
    writer, _ = make_writer(
        session_factory, embedder, proposals(add("The owner is allergic to penicillin."))
    )

    result = await writer.process(work)

    ((memory_id, content, status),) = await memories(session_factory)
    assert (content, status) == ("The owner is allergic to penicillin.", "active")
    assert result.applied.added == (memory_id,) and result.proposed == 1
    assert await events(session_factory, memory_id) == [
        ("created", REASONS["add"], work.owner_message_id)
    ]
    async with session_factory() as db:
        row = (await db.execute(select(Memory))).scalar_one()
        assert (row.source_message_id, row.source_kind, row.created_at, row.kind) == (
            work.owner_message_id,
            "owner",
            NOW,
            "fact",
        )


async def test_an_events_end_is_stored_as_the_start_of_the_next_day_in_the_owners_zone(
    session_factory, embedder
):
    writer, _ = make_writer(
        session_factory, embedder, proposals(add("Dentist on 3 March 2027.", "event", "2027-03-03"))
    )

    await writer.process(await job(session_factory, "Dentist on 3 March."))

    async with session_factory() as db:
        row = (await db.execute(select(Memory))).scalar_one()
    assert row.valid_until == datetime(2027, 3, 4, 0, 0, tzinfo=UTC)


async def test_a_changed_fact_replaces_the_stored_one_and_keeps_it_as_history(
    session_factory, embedder
):
    old = await stored(session_factory, embedder, "The owner lives in Lisbon.")
    work = await job(session_factory)
    writer, _ = make_writer(
        session_factory,
        embedder,
        proposals(add("The owner lives in Porto.")),
        decide("replaces", old),
    )

    result = await writer.process(work)

    rows = await memories(session_factory)
    assert [(c, s) for _, c, s in rows] == [
        ("The owner lives in Lisbon.", "superseded"),
        ("The owner lives in Porto.", "active"),
    ]
    assert result.applied.superseded == (old,)
    assert (await events(session_factory, old))[-1] == (
        "superseded",
        REASONS["supersede"],
        work.owner_message_id,
    )


async def test_a_repeat_writes_nothing(session_factory, embedder):
    old = await stored(session_factory, embedder, "The owner is vegetarian.", kind="preference")
    writer, _ = make_writer(
        session_factory,
        embedder,
        proposals(add("The owner is vegetarian.", "preference")),
        decide("same", old),
    )

    result = await writer.process(await job(session_factory, "I'm vegetarian."))

    assert len(await memories(session_factory)) == 1
    assert result.applied.added == () and len(await events(session_factory, old)) == 1


async def test_a_forget_request_deletes_what_the_model_names(session_factory, embedder):
    surgery = await stored(session_factory, embedder, "The owner had knee surgery in March.")
    keep = await stored(session_factory, embedder, "The owner is vegetarian.")
    work = await job(session_factory, "Forget the surgery.")
    writer, _ = make_writer(
        session_factory,
        embedder,
        proposals(forget("The owner's knee surgery in March.")),
        forgets(surgery),
    )

    result = await writer.process(work)

    assert {i: s for i, _, s in await memories(session_factory)} == {
        surgery: "deleted",
        keep: "active",
    }
    assert result.applied.deleted == (surgery,)
    assert (await events(session_factory, surgery))[-1] == (
        "deleted",
        REASONS["delete"],
        work.owner_message_id,
    )


async def test_a_secret_is_filtered_before_anything_else_sees_it(session_factory, embedder):
    writer, llm = make_writer(
        session_factory,
        embedder,
        proposals(
            add("The owner's card is 4111 1111 1111 1111."), add("The owner travels to Rome.")
        ),
    )

    result = await writer.process(await job(session_factory, "card 4111 1111 1111 1111; Rome"))

    assert [c for _, c, _ in await memories(session_factory)] == ["The owner travels to Rome."]
    assert result.filtered == ("card",)
    assert len(llm.calls) == 1  # no reconciliation call, and the card was never embedded
    assert all("4111" not in text for call in embedder.calls for text in call)


async def test_a_turn_with_nothing_to_remember_writes_and_embeds_nothing(session_factory, embedder):
    writer, llm = make_writer(session_factory, embedder, proposals())

    result = await writer.process(await job(session_factory, "Thanks!"))

    assert await memories(session_factory) == []
    assert (result.proposed, len(llm.calls), embedder.calls) == (0, 1, [])


async def test_the_extractor_gets_the_message_the_context_the_reply_and_the_date(
    session_factory, embedder
):
    writer, llm = make_writer(session_factory, embedder, proposals())
    context = (ContextMessage("owner", "A dog?"), ContextMessage("assistant", "What breed?"))

    await writer.process(await job(session_factory, "A beagle.", context=context, reply="Nice."))

    (call,) = llm.calls
    assert "1. A beagle." in call.prompt and "owner: A dog?" in call.prompt
    assert "<assistant_reply>\nNice.\n</assistant_reply>" in call.prompt
    assert "Today is Wednesday 28 October 2026 (Europe/Lisbon)" in call.system


async def test_the_result_adds_up_the_tokens_of_both_model_steps(session_factory, embedder):
    old = await stored(session_factory, embedder, "The owner lives in Lisbon.")
    writer, _ = make_writer(
        session_factory,
        embedder,
        proposals(add("The owner lives in Porto."), usage=Usage(500, 40)),
        decide("replaces", old, usage=Usage(300, 5)),
    )

    result = await writer.process(await job(session_factory))

    assert result.usage == Usage(800, 45)


async def test_a_proposal_the_answer_could_not_back_is_counted_not_written(
    session_factory, embedder
):
    await stored(session_factory, embedder, "The owner lives in Lisbon.")
    writer, _ = make_writer(
        session_factory,
        embedder,
        proposals(add("The owner lives in Porto."), add("nonsense", kind="mood")),
        decide("replaces", 9999),
    )

    result = await writer.process(await job(session_factory))

    assert result.dropped == 2  # one the extractor could not use, one with a stray id
    assert len(await memories(session_factory)) == 1


async def test_a_failing_model_writes_nothing_and_raises(session_factory, embedder):
    writer, _ = make_writer(session_factory, embedder, LLMError("down", retryable=True))

    with pytest.raises(LLMError):
        await writer.process(await job(session_factory))

    assert await memories(session_factory) == []


async def test_a_failing_embedder_writes_nothing_and_raises(session_factory):
    class Broken:
        async def embed(self, texts):
            raise RuntimeError("embeddings down")

    writer = MemoryWriter(
        session_factory,
        ScriptedStructured(proposals(add("The owner has a cat."))),
        Broken(),
        timezone=TZ,
    )

    with pytest.raises(RuntimeError):
        await writer.process(await job(session_factory))

    assert await memories(session_factory) == []


# The queue and its worker ----------------------------------------------------------------------


async def test_a_submitted_job_is_processed_by_the_worker(session_factory, embedder):
    writer, _ = make_writer(session_factory, embedder, proposals(add("The owner has a cat.")))

    writer.submit(await job(session_factory, "I have a cat."))
    await writer.drain()

    assert [c for _, c, _ in await memories(session_factory)] == ["The owner has a cat."]
    await writer.close()


async def test_jobs_run_in_order_so_a_later_one_sees_what_an_earlier_one_wrote(
    session_factory, embedder
):
    first = await job(session_factory, "I have a cat.")
    second = await job(session_factory, "My cat is at home.")
    writer, llm = make_writer(
        session_factory,
        embedder,
        proposals(add("The owner has a cat.")),
        proposals(add("The owner has a cat.")),
        decide("same", 1),
    )

    writer.submit(first)
    writer.submit(second)
    await writer.drain()

    assert len(await memories(session_factory)) == 1  # the second found the first one's memory
    assert len(llm.calls) == 3
    await writer.close()


async def test_a_failed_job_is_logged_without_the_text_and_the_worker_carries_on(
    session_factory, embedder, caplog
):
    broken = await job(session_factory, "My secret plan is to move to Lisbon.")
    fine = await job(session_factory, "I have a cat.")
    writer, _ = make_writer(
        session_factory,
        embedder,
        LLMError("the model is down", retryable=True),
        proposals(add("The owner has a cat.")),
    )

    with caplog.at_level(logging.ERROR, logger="app.memory.writer"):
        writer.submit(broken)
        writer.submit(fine)
        await writer.drain()

    assert [c for _, c, _ in await memories(session_factory)] == ["The owner has a cat."]
    assert f"message {broken.owner_message_id} failed" in caplog.text
    assert "Lisbon" not in caplog.text and "secret plan" not in caplog.text
    await writer.close()


async def test_a_full_queue_drops_the_new_job_and_says_so(
    session_factory, embedder, monkeypatch, caplog
):
    monkeypatch.setattr(writer_module, "MAX_PENDING", 1)
    writer, llm = make_writer(
        session_factory, embedder, proposals(add("The owner has a cat.")), proposals()
    )
    kept, lost = await job(session_factory, "one"), await job(session_factory, "two")

    with caplog.at_level(logging.WARNING, logger="app.memory.writer"):
        writer.submit(kept)
        writer.submit(lost)  # no await between, so the worker has not taken the first yet
        await writer.drain()

    assert len(llm.calls) == 1
    assert f"message {lost.owner_message_id}" in caplog.text
    await writer.close()


async def test_close_lets_queued_jobs_finish_and_stops_the_worker(session_factory, embedder):
    writer, _ = make_writer(session_factory, embedder, proposals(add("The owner has a cat.")))

    writer.submit(await job(session_factory, "I have a cat."))
    await writer.close()

    assert len(await memories(session_factory)) == 1
    assert writer._task is None


async def test_close_gives_up_on_a_job_that_will_not_finish(session_factory, embedder, caplog):
    class Stuck:
        async def generate(self, **kwargs):
            await asyncio.sleep(30)

    writer = MemoryWriter(session_factory, Stuck(), embedder, timezone=TZ)
    writer.submit(await job(session_factory))
    await asyncio.sleep(0.05)

    with caplog.at_level(logging.WARNING, logger="app.memory.writer"):
        await asyncio.wait_for(writer.close(timeout=0.05), 2)

    assert "not finished at shutdown" in caplog.text
    assert writer._task is None


async def test_closing_a_writer_that_never_ran_is_fine(session_factory, embedder):
    writer, _ = make_writer(session_factory, embedder)

    await writer.close()


async def test_a_writer_that_was_closed_starts_again_on_the_next_job(session_factory, embedder):
    writer, _ = make_writer(
        session_factory,
        embedder,
        proposals(add("The owner has a cat.")),
        proposals(add("Silva treats my teeth.")),
    )

    writer.submit(await job(session_factory, "cat"))
    await writer.close()
    writer.submit(await job(session_factory, "dog"))
    await writer.close()

    assert len(await memories(session_factory)) == 2


async def test_reconciliation_is_given_the_owners_own_words(session_factory, embedder):
    old = await stored(session_factory, embedder, "The owner lives in Lisbon.")
    writer, llm = make_writer(
        session_factory,
        embedder,
        proposals(add("The owner lives in Porto.")),
        decide("replaces", old),
    )

    await writer.process(await job(session_factory, "Actually, I moved to Porto."))

    assert "<owner_said>\nActually, I moved to Porto.\n</owner_said>" in llm.calls[1].prompt


async def test_a_worker_that_was_cancelled_from_outside_is_replaced_by_the_next_submit(
    session_factory, embedder
):
    writer, _ = make_writer(
        session_factory, embedder, proposals(), proposals(add("The owner has a cat."))
    )
    writer.submit(await job(session_factory, "first"))  # starts the worker, which then waits
    await asyncio.sleep(0.05)
    dead = writer._task
    dead.cancel()
    await asyncio.gather(dead, return_exceptions=True)

    writer.submit(await job(session_factory, "I have a cat."))
    await writer.drain()

    assert writer._task is not dead
    assert [c for _, c, _ in await memories(session_factory)] == ["The owner has a cat."]
    await writer.close()
