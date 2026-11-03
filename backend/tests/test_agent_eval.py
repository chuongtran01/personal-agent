import json

import pytest
from agent_cases import AgentCase
from agent_eval import CaseRun, memory_effect, percentile, run_case, summarize
from fake_embedder import FakeEmbedder
from memory_cases import EVAL_NOW
from scripted_llm import ScriptedLLM, reply
from sqlalchemy import func, select

from app.agent.llm import LLMError
from app.agent.loop import AgentLoop
from app.agent.messages import UserMessage
from app.memory.seed import load_seed, seed
from app.models import ChatMessage, Memory

SEED = {m.key: m for m in load_seed()}

ROW = {
    "id": "a900",
    "category": "recall_used",
    "prior_turns": [],
    "message": "Where does the owner live? Porto",
    "must_recall": ["home-city"],
    "must_not_recall": ["home-city-old"],
    "must_include": [["Porto"]],
    "must_not_include": ["Alfama"],
    "expect_unknown": False,
    "trap_id": None,
    "note": None,
}


def case(**change):
    return AgentCase.model_validate({**ROW, **change})


@pytest.fixture
async def world(session_factory):
    async with session_factory() as db:
        ids = await seed(db, FakeEmbedder(), load_seed())
    return {memory_id: key for key, memory_id in ids.items()}


def loop_for(session_factory, *scripts, recall_enabled=True):
    llm = ScriptedLLM(*scripts)
    loop = AgentLoop(
        session_factory,
        llm,
        FakeEmbedder(),
        clock=lambda: EVAL_NOW,
        recall_mode="keyword",
        recall_enabled=recall_enabled,
        retry_delay=0,
    )
    return loop, llm


async def play(session_factory, world, c, *scripts, memory=True):
    loop, llm = loop_for(session_factory, *scripts, recall_enabled=memory)
    run = await run_case(loop, session_factory, c, 1, SEED, world)
    return run, llm


# One run -----------------------------------------------------------------------------------


async def test_a_passing_run_records_what_was_recalled_and_said(session_factory, world):
    run, _ = await play(session_factory, world, case(), reply("You live in Porto."))

    assert run.passed and run.failures == ()
    assert "home-city" in run.recalled and run.recalled_past == ()
    assert run.answer == "You live in Porto."
    assert run.recall_ok is True and run.error is None
    assert run.input_tokens == 10 and run.output_tokens == 5 and run.steps == 1
    assert run.total_seconds > 0


async def test_a_wrong_answer_fails_the_run(session_factory, world):
    run, _ = await play(session_factory, world, case(), reply("You live in Alfama."))

    assert not run.passed
    assert set(run.failures) == {"missing_word:Porto", "forbidden_word:Alfama"}


async def test_with_memory_off_nothing_is_recalled_and_the_required_memory_is_missed(
    session_factory, world
):
    run, llm = await play(session_factory, world, case(), reply("I don't know."), memory=False)

    assert run.recalled == () and run.memory is False
    assert not run.passed and "not_recalled:home-city" in run.failures
    assert "No stored memory matched" in llm.calls[0].system


async def test_earlier_turns_are_played_in_the_same_chat_before_the_judged_message(
    session_factory, world
):
    c = case(prior_turns=["Hello there"], category="follow_up")

    run, llm = await play(session_factory, world, c, reply("Hi."), reply("You live in Porto."))

    assert run.passed
    assert llm.calls[1].messages[0] == UserMessage(text="Hello there")
    assert llm.calls[1].messages[-1] == UserMessage(text=c.message)


async def test_a_turn_that_fails_is_a_failed_run_with_its_code(session_factory, world):
    c = case(must_recall=[], must_not_recall=[], must_include=[], must_not_include=["Bruno"])

    run, _ = await play(session_factory, world, c, [LLMError("down", retryable=False)])

    assert not run.passed and run.error == "model_error"
    assert run.failures == ("turn_error:model_error",)
    assert run.input_tokens == 0


async def test_an_earlier_turn_that_fails_stops_the_case(session_factory, world):
    c = case(prior_turns=["hi"], category="follow_up")

    run, llm = await play(session_factory, world, c, [LLMError("down", retryable=False)])

    assert run.error == "model_error" and len(llm.calls) == 1


async def test_every_run_gets_a_fresh_chat(session_factory, world):
    loop, _ = loop_for(session_factory, reply("one"), reply("two"))

    await run_case(loop, session_factory, case(), 1, SEED, world)
    await run_case(loop, session_factory, case(), 2, SEED, world)

    async with session_factory() as db:
        counts = (
            await db.execute(
                select(ChatMessage.session_id, func.count()).group_by(ChatMessage.session_id)
            )
        ).all()
    assert sorted(count for _, count in counts) == [2, 2]


async def test_a_leak_is_reported_with_its_key(session_factory, world, monkeypatch):
    # Make recall hand back a deleted memory, as a broken filter would
    from app.memory import recall as recall_module

    real = recall_module.recall

    async def leaky(*args, **kwargs):
        recalled = await real(*args, **kwargs)
        surgery = next(i for i, k in world.items() if k == "surgery")
        from dataclasses import replace

        extra = replace(recalled.current[0], memory_id=surgery)
        return type(recalled)(current=(*recalled.current, extra), past=recalled.past)

    monkeypatch.setattr("app.agent.loop.recall", leaky)

    run, _ = await play(session_factory, world, case(), reply("You live in Porto."))

    assert run.leaks == ("surgery",) and not run.passed


# Summaries ---------------------------------------------------------------------------------


def make(id, category="recall_used", run=1, memory=True, passed=True, **kwargs):
    base = {
        "id": id,
        "category": category,
        "run": run,
        "memory": memory,
        "passed": passed,
        "failures": () if passed else ("missing_word:x",),
        "leaks": (),
        "recall_ok": True,
        "recalled": (),
        "recalled_past": (),
        "answer": "",
        "error": None,
        "input_tokens": 400,
        "output_tokens": 20,
        "steps": 1,
        "recall_seconds": 0.5,
        "total_seconds": 2.0,
    }
    return CaseRun(**{**base, **kwargs})


def test_the_summary_counts_passes_by_category_and_leaks():
    runs = [
        make("a1", passed=True),
        make("a2", passed=False),
        make("a3", category="false_memory", passed=True, recall_ok=None),
        make(
            "a4",
            category="false_memory",
            passed=False,
            recall_ok=None,
            leaks=("surgery", "ex-partner"),
        ),
    ]

    s = summarize(runs)

    assert (s["runs"], s["passed"], s["leaks"]) == (4, 2, 2)
    assert s["by_category"] == {
        "false_memory": {"runs": 2, "passed": 1},
        "recall_used": {"runs": 2, "passed": 1},
    }
    assert s["recall"] == {"runs": 2, "all_required_recalled": 2}


def test_a_case_that_passes_in_some_runs_only_is_flaky_and_one_that_never_passes_is_not():
    runs = [
        make("steady", run=1),
        make("steady", run=2),
        make("flaky", run=1, passed=True),
        make("flaky", run=2, passed=False),
        make("broken", run=1, passed=False),
        make("broken", run=2, passed=False),
    ]

    s = summarize(runs)

    assert s["flaky"] == ["flaky"] and s["always_failing"] == ["broken"]


def test_tokens_and_latency_leave_out_turns_that_never_answered():
    runs = [
        make("a1", total_seconds=1.0, recall_seconds=0.2),
        make("a2", total_seconds=3.0, recall_seconds=0.4),
        make(
            "a3",
            passed=False,
            error="model_error",
            input_tokens=0,
            output_tokens=0,
            total_seconds=0,
        ),
    ]

    s = summarize(runs)

    assert s["errors"] == 1
    assert s["tokens_per_message"] == {"input": 400, "output": 20}
    assert s["seconds"]["total_p50"] == 1.0 and s["seconds"]["total_p95"] == 3.0


def test_dollars_appear_only_when_prices_are_given():
    runs = [make("a1")]

    assert "dollars_per_message" not in summarize(runs)
    assert summarize(runs, price_in=2.0, price_out=10.0)["dollars_per_message"] == pytest.approx(
        (400 * 2.0 + 20 * 10.0) / 1e6
    )


def test_percentiles_are_nearest_rank_and_safe_when_empty():
    assert percentile([], 0.5) == 0.0
    assert percentile([5.0], 0.95) == 5.0
    assert percentile([4, 1, 3, 2], 0.5) == 2
    assert percentile(list(range(1, 101)), 0.95) == 95


def test_the_memory_effect_compares_only_the_cases_that_need_a_seeded_fact():
    on = [make("needs"), make("free", recall_ok=None)]
    off = [make("needs", memory=False, passed=False), make("free", memory=False, recall_ok=None)]

    effect = memory_effect(on, off)

    assert effect == {
        "cases": 1,
        "on": {"runs": 1, "passed": 1},
        "off": {"runs": 1, "passed": 0},
    }


def test_the_results_file_can_hold_a_run():
    from dataclasses import asdict

    assert json.dumps(asdict(make("a1")))


# Cases that can write memory ----------------------------------------------------------------------


def writer_for(session_factory, *answers):
    from scripted_llm import ScriptedStructured
    from word_embedder import WordEmbedder

    from app.memory.writer import MemoryWriter

    return MemoryWriter(
        session_factory, ScriptedStructured(*answers), WordEmbedder(), timezone="UTC"
    )


def learns(*contents):
    """What the extractor answers: each content proposed as a new fact."""
    from scripted_llm import answer

    return answer(
        {
            "proposals": [
                {"op": "add", "kind": "fact", "content": c, "valid_until": None} for c in contents
            ]
        }
    )


async def learning_loop(session_factory, llm_scripts, *answers):
    writer = writer_for(session_factory, *answers)
    loop = AgentLoop(
        session_factory,
        ScriptedLLM(*llm_scripts),
        FakeEmbedder(),
        clock=lambda: EVAL_NOW,
        recall_mode="keyword",
        retry_delay=0,
        memory_writer=writer,
    )
    return loop, writer


async def taken(session_factory):
    from app.memory.seed import snapshot

    async with session_factory() as db:
        return await snapshot(db)


async def memory_count(session_factory):
    async with session_factory() as db:
        return (await db.execute(select(func.count()).select_from(Memory))).scalar_one()


def describe(saved, rows, world):
    from agent_eval import describe_changes

    return describe_changes(saved, rows, world)


async def test_what_a_case_wrote_is_recorded_and_the_next_case_starts_clean(session_factory, world):
    saved = await taken(session_factory)
    loop, writer = await learning_loop(
        session_factory,
        [reply("Noted."), reply("You live in Porto.")],
        learns("The owner has a cat called Miso."),
        learns(),
    )

    first = await run_case(
        loop, session_factory, case(message="I have a cat called Miso."), 1, SEED, world, saved
    )
    wrote_then = await memory_count(session_factory)
    second = await run_case(loop, session_factory, case(), 2, SEED, world, saved)

    assert first.written == ("added: The owner has a cat called Miso. [active]",)
    assert wrote_then == 43
    assert second.written == () and await memory_count(session_factory) == 42
    await writer.close()


async def test_a_case_starts_from_the_snapshot_even_if_something_else_changed_memory(
    session_factory, world
):
    saved = await taken(session_factory)
    async with session_factory() as db:
        db.add(Memory(kind="fact", content="a stray memory", status="active"))
        await db.commit()
    loop, writer = await learning_loop(session_factory, [reply("ok")], learns())

    run = await run_case(loop, session_factory, case(message="hello"), 1, SEED, world, saved)

    assert await memory_count(session_factory) == 42 and run.written == ()
    await writer.close()


async def test_each_turn_waits_for_its_memory_write_so_the_next_message_can_use_it(
    session_factory, world
):
    saved = await taken(session_factory)
    loop, writer = await learning_loop(
        session_factory,
        [reply("Noted."), reply("A cat called Miso.")],
        learns("The owner has a cat called Miso."),
    )
    c = case(
        prior_turns=["I have a cat called Miso."],
        message="What is my cat called?",
        category="follow_up",
        must_recall=[],
        must_not_recall=[],
        must_include=[["Miso"]],
        must_not_include=[],
    )

    run = await run_case(loop, session_factory, c, 1, SEED, world, saved)

    assert run.passed
    assert (
        "The owner has a cat called Miso." in loop._llm.calls[1].system
    )  # the second turn recalled it
    await writer.close()


async def test_the_last_write_has_finished_before_the_case_returns(session_factory, world):
    saved = await taken(session_factory)
    loop, writer = await learning_loop(
        session_factory, [reply("Noted.")], learns("The owner has a cat.")
    )

    await run_case(loop, session_factory, case(message="I have a cat."), 1, SEED, world, saved)

    assert await memory_count(session_factory) == 43  # not 42 with the write still queued
    await writer.close()


async def test_without_a_snapshot_nothing_is_restored_or_recorded(session_factory, world):
    async with session_factory() as db:
        db.add(Memory(kind="fact", content="kept", status="active"))
        await db.commit()

    run, _ = await play(session_factory, world, case(), reply("You live in Porto."))

    assert run.written == () and await memory_count(session_factory) == 43


async def test_a_memory_the_agent_wrote_and_recalled_in_the_same_case_is_not_a_leak(
    session_factory, world
):
    saved = await taken(session_factory)
    loop, writer = await learning_loop(
        session_factory,
        [reply("Noted."), reply("A cat called Miso.")],
        learns("The owner has a cat called Miso."),
    )
    c = case(
        prior_turns=["I have a cat called Miso."],
        message="What is my cat called?",
        category="follow_up",
        must_recall=[],
        must_not_recall=[],
        must_include=[],
        must_not_include=["Bruno"],
    )

    run = await run_case(loop, session_factory, c, 1, SEED, world, saved)

    assert any(key.startswith("id:") for key in run.recalled) and run.leaks == ()
    await writer.close()


# What changed, in words ---------------------------------------------------------------------------


async def test_changes_name_new_rows_and_what_happened_to_seeded_ones(session_factory, world):
    saved = await taken(session_factory)
    home, other = world_ids(world, "home-city", "manager")
    rows = [
        (memory_id, content, status)
        for memory_id, content, status in await current_rows(session_factory)
        if memory_id != other
    ]
    rows = [(i, c, "superseded" if i == home else s) for i, c, s in rows]
    rows.append((9001, "The owner has a cat.", "active"))

    lines = describe(saved, rows, world)

    assert lines == (
        "added: The owner has a cat. [active]",
        "superseded: home-city (was active)",
        "removed: manager",
    )


async def test_nothing_changed_is_nothing_to_report(session_factory, world):
    saved = await taken(session_factory)

    assert describe(saved, await current_rows(session_factory), world) == ()


async def current_rows(session_factory):
    async with session_factory() as db:
        found = await db.execute(select(Memory.id, Memory.content, Memory.status))
        return [tuple(row) for row in found.all()]


def world_ids(world, *keys):
    by_key = {key: memory_id for memory_id, key in world.items()}
    return [by_key[key] for key in keys]


# The options ---------------------------------------------------------------------------------------


def test_writes_need_one_case_at_a_time():
    from agent_eval import check_options
    from memory_retrieval_eval import EvalError

    check_options(True, 1)
    check_options(False, 8)
    with pytest.raises(EvalError, match="--concurrency 1"):
        check_options(True, 2)


def test_the_summary_counts_what_was_written_and_the_report_shows_it_only_then(capsys):
    from agent_eval import print_report

    runs = [
        make("a1", written=("added: a [active]", "deleted: x (was active)")),
        make("a2"),
        make("a3", written=("added: b [active]",)),
    ]

    summary = summarize(runs)
    print_report({"on": summary}, None)
    with_writes = capsys.readouterr().out
    print_report({"on": summarize([make("a4")])}, None)
    without = capsys.readouterr().out

    assert summary["written"] == 3 and "memory changes" in with_writes
    assert "memory changes" not in without
