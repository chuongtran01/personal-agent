import asyncio
from datetime import UTC, datetime

import pytest
from fake_embedder import FakeEmbedder
from memory_cases import EVAL_NOW
from scripted_llm import ScriptedLLM, reply, tool_call
from sqlalchemy import select, text

from app.agent import store
from app.agent.llm import LLMError, TextDelta, ToolSpec, Usage
from app.agent.loop import (
    AgentDone,
    AgentLoop,
    ChatFull,
    EmptyMessage,
    MemoryRecalled,
    MessageTooLong,
    SessionNotFound,
    TurnError,
    TurnInProgress,
)
from app.agent.messages import AssistantMessage, ToolResultMessage, UserMessage
from app.config import settings
from app.memory.seed import load_seed, seed
from app.models import Memory

NOW = EVAL_NOW


class FakeTools:
    def __init__(self, results=None, fail=False):
        self.results = results or {}
        self.fail = fail
        self.calls = []

    def specs(self):
        return [ToolSpec("lookup", "Find a thing", {"type": "object", "properties": {}})]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if self.fail:
            raise RuntimeError("secret detail that must not reach the model")
        return self.results[name]


class BrokenEmbedder:
    async def embed(self, texts):
        raise RuntimeError("embedding service down")


@pytest.fixture
async def seeded(session_factory):
    async with session_factory() as db:
        ids = await seed(db, FakeEmbedder(), load_seed())
    return ids


@pytest.fixture
async def chat(session_factory):
    async with session_factory() as db:
        return await store.create_session(db)


def make_loop(session_factory, llm, **kwargs):
    kwargs.setdefault("recall_mode", "keyword")
    kwargs.setdefault("retry_delay", 0)
    return AgentLoop(session_factory, llm, FakeEmbedder(), clock=lambda: NOW, **kwargs)


async def play(loop, chat, text):
    turn = await loop.start(chat, text)
    return [event async for event in turn.events()]


def ids(results):
    return tuple(r.memory_id for r in results)


def bare(message):
    """A stored reply without the ids of what was recalled for it."""
    if isinstance(message, AssistantMessage):
        return message.model_copy(update={"recalled": None, "recalled_past": None})
    return message


async def stored(session_factory, chat):
    async with session_factory() as db:
        return await store.load(db, chat)


# A normal turn -----------------------------------------------------------------------------


async def test_a_turn_recalls_prompts_streams_and_stores_the_answer(session_factory, seeded, chat):
    llm = ScriptedLLM(reply("You live ", "in Porto."))

    events = await play(make_loop(session_factory, llm), chat, "Where does the owner live? Porto")

    assert isinstance(events[0], MemoryRecalled)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["You live ", "in Porto."]
    assert isinstance(events[-1], AgentDone)
    assert await stored(session_factory, chat) == [
        UserMessage(text="Where does the owner live? Porto"),
        AssistantMessage(
            text="You live in Porto.", recalled=ids(events[0].current), recalled_past=()
        ),
    ]
    (call,) = llm.calls
    assert "lives in Porto" in call.system
    assert call.messages == (UserMessage(text="Where does the owner live? Porto"),)


async def test_the_prompt_uses_the_loops_clock(session_factory, seeded, chat):
    llm = ScriptedLLM(reply("ok"))

    await play(make_loop(session_factory, llm), chat, "hello")

    assert "Wednesday 28 October 2026" in llm.calls[0].system


async def test_the_done_event_reports_the_answer_usage_and_timings(session_factory, seeded, chat):
    llm = ScriptedLLM(reply("ok", usage=Usage(120, 30)))

    events = await play(make_loop(session_factory, llm), chat, "hello")

    done = events[-1]
    assert (done.usage, done.steps) == (Usage(120, 30), 1)
    assert done.message_id == (await stored_ids(session_factory, chat))[-1]
    assert 0 <= done.recall_seconds <= done.total_seconds


async def stored_ids(session_factory, chat):
    from app.models import ChatMessage

    async with session_factory() as db:
        rows = await db.execute(
            select(ChatMessage.id).where(ChatMessage.session_id == chat).order_by(ChatMessage.id)
        )
        return list(rows.scalars())


async def test_memories_that_went_into_the_prompt_are_marked_used(session_factory, seeded, chat):
    llm = ScriptedLLM(reply("ok"))
    loop = make_loop(session_factory, llm)

    events = await play(loop, chat, "Where does the owner live? Porto")

    recalled = {m.memory_id for m in events[0].current}
    assert seeded["home-city"] in recalled
    async with session_factory() as db:
        rows = (await db.execute(select(Memory.id, Memory.last_used_at))).all()
    assert {i for i, used in rows if used is not None} == recalled
    assert {used for _, used in rows if used is not None} == {NOW}


async def test_the_memory_event_comes_before_any_text(session_factory, seeded, chat):
    events = await play(make_loop(session_factory, ScriptedLLM(reply("a"))), chat, "dog Pepa")

    kinds = [type(e).__name__ for e in events]
    assert kinds.index("MemoryRecalled") < kinds.index("TextDelta")


async def test_a_question_about_the_past_puts_replaced_memories_in_the_past_list(
    session_factory, seeded, chat
):
    llm = ScriptedLLM(reply("You used to live in Lisbon."))

    events = await play(
        make_loop(session_factory, llm), chat, "Where did the owner live before? Lisbon Alfama"
    )

    past = {m.memory_id for m in events[0].past}
    assert seeded["home-city-old"] in past
    assert "No longer true or already happened:" in llm.calls[0].system


async def test_turning_memory_off_sends_no_memories_and_marks_none(session_factory, seeded, chat):
    llm = ScriptedLLM(reply("I don't know."))

    events = await play(
        make_loop(session_factory, llm, recall_enabled=False),
        chat,
        "Where does the owner live? Porto",
    )

    assert events[0] == MemoryRecalled((), ())
    assert "No stored memory matched this message." in llm.calls[0].system
    assert "Porto" not in llm.calls[0].system
    async with session_factory() as db:
        used = (await db.execute(select(Memory.last_used_at))).scalars().all()
    assert set(used) == {None}


async def test_a_second_turn_sees_the_conversation_and_recalls_from_the_last_two_messages(
    session_factory, seeded, chat
):
    llm = ScriptedLLM(reply("Tiago is your partner."), reply("On 14 February."))
    loop = make_loop(session_factory, llm)

    await play(loop, chat, "Tell me about Tiago")
    await play(loop, chat, "And his birthday?")

    second = llm.calls[1]
    assert tuple(bare(m) for m in second.messages) == (
        UserMessage(text="Tell me about Tiago"),
        AssistantMessage(text="Tiago is your partner."),
        UserMessage(text="And his birthday?"),
    )
    assert "14 February" in second.system  # found through the earlier message


# Refusals ----------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   \n"])
async def test_an_empty_message_is_refused_and_nothing_is_stored(session_factory, chat, text):
    with pytest.raises(EmptyMessage):
        await make_loop(session_factory, ScriptedLLM()).start(chat, text)

    assert await stored(session_factory, chat) == []


async def test_a_message_over_the_limit_is_refused(session_factory, chat, monkeypatch):
    monkeypatch.setattr(settings, "max_message_chars", 10)
    loop = make_loop(session_factory, ScriptedLLM())

    with pytest.raises(MessageTooLong):
        await loop.start(chat, "x" * 11)
    await loop.start(chat, "x" * 10)  # exactly the limit is fine


async def test_an_unknown_chat_is_refused(session_factory):
    import uuid

    with pytest.raises(SessionNotFound):
        await make_loop(session_factory, ScriptedLLM()).start(uuid.uuid4(), "hi")


async def test_a_full_chat_is_refused(session_factory, chat, monkeypatch):
    monkeypatch.setattr(settings, "max_session_messages", 2)
    llm = ScriptedLLM(reply("one"))
    loop = make_loop(session_factory, llm)
    await play(loop, chat, "first")  # now holds a user and an assistant message

    with pytest.raises(ChatFull):
        await loop.start(chat, "second")

    assert len(await stored(session_factory, chat)) == 2


async def test_a_refusal_gives_the_chat_back(session_factory, chat, monkeypatch):
    monkeypatch.setattr(settings, "max_message_chars", 5)
    loop = make_loop(session_factory, ScriptedLLM(reply("ok")))

    with pytest.raises(MessageTooLong):
        await loop.start(chat, "too long for this")
    monkeypatch.setattr(settings, "max_message_chars", 4000)

    assert isinstance((await play(loop, chat, "hi"))[-1], AgentDone)


# One turn at a time ------------------------------------------------------------------------


async def test_a_second_message_while_a_turn_is_open_is_refused(session_factory, chat):
    loop = make_loop(session_factory, ScriptedLLM(reply("one"), reply("two")))
    turn = await loop.start(chat, "first")

    with pytest.raises(TurnInProgress):
        await loop.start(chat, "second")
    assert [e async for e in turn.events()]
    assert isinstance((await play(loop, chat, "second"))[-1], AgentDone)


async def test_the_refused_message_is_not_stored(session_factory, chat):
    loop = make_loop(session_factory, ScriptedLLM(reply("one")))
    turn = await loop.start(chat, "first")

    with pytest.raises(TurnInProgress):
        await loop.start(chat, "second")
    [e async for e in turn.events()]

    assert [m.text for m in await stored(session_factory, chat) if isinstance(m, UserMessage)] == [
        "first"
    ]


async def test_two_chats_can_answer_at_the_same_time(session_factory):
    async with session_factory() as db:
        one, two = await store.create_session(db), await store.create_session(db)
    loop = make_loop(session_factory, ScriptedLLM(reply("a"), reply("b")))
    first = await loop.start(one, "hi")

    second = await loop.start(two, "hi")

    assert [e async for e in first.events()] and [e async for e in second.events()]


async def test_closing_a_turn_that_was_never_read_gives_the_chat_back(session_factory, chat):
    loop = make_loop(session_factory, ScriptedLLM(reply("ok")))
    turn = await loop.start(chat, "hi")

    turn.close()
    turn.close()  # twice is fine

    await loop.start(chat, "again")


async def test_stopping_the_event_stream_early_gives_the_chat_back(session_factory, chat):
    loop = make_loop(session_factory, ScriptedLLM(reply("a", "b", "c")))
    events = (await loop.start(chat, "hi")).events()

    await events.__anext__()
    await events.aclose()

    await loop.start(chat, "again")


async def test_a_turn_runs_to_the_end_in_a_background_task_nobody_reads(
    session_factory, seeded, chat
):
    """The API pumps events into a queue; a reader that leaves must not lose the turn."""
    loop = make_loop(session_factory, ScriptedLLM(reply("Porto.")))
    turn = await loop.start(chat, "Where does the owner live? Porto")
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        async for event in turn.events():
            queue.put_nowait(event)

    await asyncio.create_task(pump())

    assert (await stored(session_factory, chat))[-1].text == "Porto."


# Failures ----------------------------------------------------------------------------------


async def test_a_retryable_failure_before_any_output_is_retried_once(session_factory, chat):
    llm = ScriptedLLM([LLMError("busy", retryable=True)], reply("worked"))

    events = await play(make_loop(session_factory, llm), chat, "hi")

    assert len(llm.calls) == 2
    assert isinstance(events[-1], AgentDone)
    assert bare((await stored(session_factory, chat))[-1]) == AssistantMessage(text="worked")


async def test_a_retryable_failure_twice_ends_the_turn_with_an_error(session_factory, chat):
    llm = ScriptedLLM([LLMError("busy", retryable=True)], [LLMError("busy", retryable=True)])

    events = await play(make_loop(session_factory, llm), chat, "hi")

    assert len(llm.calls) == 2
    assert isinstance(events[-1], TurnError) and events[-1].code == "model_error"


async def test_a_failure_that_cannot_help_is_not_retried(session_factory, chat):
    llm = ScriptedLLM([LLMError("bad request", retryable=False)])

    events = await play(make_loop(session_factory, llm), chat, "hi")

    assert len(llm.calls) == 1
    assert events[-1].code == "model_error"


async def test_a_failure_after_text_was_sent_is_not_retried_and_the_partial_is_not_stored(
    session_factory, chat
):
    llm = ScriptedLLM([TextDelta("par"), LLMError("dropped", retryable=True)], reply("again"))

    events = await play(make_loop(session_factory, llm), chat, "hi")

    assert len(llm.calls) == 1
    assert TextDelta("par") in events and events[-1].code == "model_error"
    assert await stored(session_factory, chat) == [UserMessage(text="hi")]


async def test_the_error_never_carries_provider_text(session_factory, chat):
    llm = ScriptedLLM([LLMError("sk-secret-key rejected", retryable=False)])

    events = await play(make_loop(session_factory, llm), chat, "hi")

    assert "sk-secret" not in events[-1].message


async def test_after_a_failed_turn_the_owner_can_resend(session_factory, chat):
    llm = ScriptedLLM([LLMError("down", retryable=False)], reply("back"))
    loop = make_loop(session_factory, llm)

    await play(loop, chat, "hi")
    events = await play(loop, chat, "hi again")

    assert isinstance(events[-1], AgentDone)
    assert llm.calls[1].messages == (UserMessage(text="hi"), UserMessage(text="hi again"))


async def test_a_recall_failure_ends_the_turn_instead_of_answering_without_memory(
    session_factory, chat
):
    llm = ScriptedLLM(reply("I don't know."))
    loop = AgentLoop(session_factory, llm, BrokenEmbedder(), recall_mode="vector", retry_delay=0)

    events = await play(loop, chat, "Where do I live?")

    assert events == [TurnError("recall_failed", "could not look up memory; please try again")]
    assert llm.calls == []
    assert await stored(session_factory, chat) == [UserMessage(text="Where do I live?")]


async def test_an_unreadable_stored_message_ends_the_turn_and_frees_the_chat(session_factory, chat):
    async with session_factory() as db:
        await db.execute(
            text("INSERT INTO chat_messages (session_id, role, content) VALUES (:s, 'user', '{}')"),
            {"s": chat},
        )
        await db.commit()
    loop = make_loop(session_factory, ScriptedLLM(reply("ok")))

    events = await play(loop, chat, "hi")

    assert events[-1].code == "history_unreadable"
    await loop.start(chat, "again")


async def test_a_failure_in_marking_memories_used_does_not_lose_the_answer(
    session_factory, seeded, chat, monkeypatch
):
    async def boom(*args, **kwargs):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr("app.agent.loop.mark_used", boom)

    events = await play(
        make_loop(session_factory, ScriptedLLM(reply("Porto."))),
        chat,
        "Where does the owner live? Porto",
    )

    assert isinstance(events[-1], AgentDone)
    assert (await stored(session_factory, chat))[-1].text == "Porto."


# Tools (the seam; none is registered in this phase) ---------------------------------------


async def test_a_tool_call_runs_the_tool_and_calls_the_model_again(session_factory, chat):
    llm = ScriptedLLM(tool_call("lookup", q="x"), reply("The answer is 42."))
    tools = FakeTools({"lookup": "42"})

    events = await play(make_loop(session_factory, llm, tools=tools), chat, "hi")

    assert tools.calls == [("lookup", {"q": "x"})]
    messages = await stored(session_factory, chat)
    assert [type(m).__name__ for m in messages] == [
        "UserMessage",
        "AssistantMessage",
        "ToolResultMessage",
        "AssistantMessage",
    ]
    assert messages[1].tool_calls[0].name == "lookup"
    assert messages[2] == ToolResultMessage(call_id="call_1", output="42")
    assert llm.calls[0].tools[0].name == "lookup"
    assert llm.calls[1].messages[-1] == messages[2]
    assert events[-1].steps == 2


async def test_usage_is_summed_over_every_model_call(session_factory, chat):
    first = tool_call("lookup")
    llm = ScriptedLLM(first, reply("done", usage=Usage(100, 20)))

    events = await play(
        make_loop(session_factory, llm, tools=FakeTools({"lookup": "x"})), chat, "hi"
    )

    assert events[-1].usage == Usage(110, 25)


async def test_an_unknown_tool_gets_an_error_result_not_a_crash(session_factory, chat):
    llm = ScriptedLLM(tool_call("nonesuch"), reply("sorry"))

    events = await play(make_loop(session_factory, llm), chat, "hi")

    result = (await stored(session_factory, chat))[2]
    assert result.output == "error: there is no tool called nonesuch"
    assert isinstance(events[-1], AgentDone)


async def test_a_tool_that_raises_gives_the_model_a_plain_error_without_the_details(
    session_factory, chat
):
    llm = ScriptedLLM(tool_call("lookup"), reply("sorry"))

    await play(make_loop(session_factory, llm, tools=FakeTools(fail=True)), chat, "hi")

    result = (await stored(session_factory, chat))[2]
    assert result.output == "error: the tool failed (RuntimeError)"
    assert "secret detail" not in result.output


async def test_the_step_limit_stops_a_model_that_keeps_calling_tools(session_factory, chat):
    llm = ScriptedLLM(*[tool_call("lookup", call_id=f"c{i}") for i in range(5)])
    loop = make_loop(session_factory, llm, tools=FakeTools({"lookup": "x"}), max_steps=3)

    events = await play(loop, chat, "hi")

    assert len(llm.calls) == 3
    assert events[-1].code == "step_limit"
    messages = await stored(session_factory, chat)
    # Every tool call that was stored has its result: the chat can still be replayed
    calls = [c.id for m in messages if isinstance(m, AssistantMessage) for c in m.tool_calls]
    results = [m.call_id for m in messages if isinstance(m, ToolResultMessage)]
    assert calls == results


def test_the_utc_default_clock_is_timezone_aware():
    from app.agent.loop import utcnow

    assert utcnow().tzinfo is not None and abs((utcnow() - datetime.now(UTC)).total_seconds()) < 5


async def test_a_loop_with_recall_off_never_calls_the_embedder(session_factory, chat):
    # A broken embedder would end the turn with recall_failed if recall ran at all
    loop = AgentLoop(
        session_factory,
        ScriptedLLM(reply("ok")),
        BrokenEmbedder(),
        clock=lambda: NOW,
        recall_enabled=False,
    )

    events = await play(loop, chat, "hi")

    assert isinstance(events[-1], AgentDone)


def test_whether_recall_runs_is_a_property_of_the_loop_and_not_of_a_message():
    import inspect

    assert "memory" not in inspect.signature(AgentLoop.start).parameters


# What is stored about the recall ----------------------------------------------------------


async def test_the_reply_that_ends_a_turn_says_which_memories_were_recalled_for_it(
    session_factory, seeded, chat
):
    events = await play(
        make_loop(session_factory, ScriptedLLM(reply("Porto."))),
        chat,
        "Where does the owner live? Porto",
    )

    user, answer = await stored(session_factory, chat)
    assert isinstance(events[0], MemoryRecalled) and events[0].current
    assert answer.recalled == ids(events[0].current) and answer.recalled_past == ()
    assert isinstance(user, UserMessage)


async def test_a_history_question_stores_the_past_memories_apart(session_factory, seeded, chat):
    events = await play(
        make_loop(session_factory, ScriptedLLM(reply("Alfama."))),
        chat,
        "Where did the owner live before? Lisbon Alfama",
    )

    answer = (await stored(session_factory, chat))[-1]
    assert answer.recalled_past == ids(events[0].past)
    assert seeded["home-city-old"] in answer.recalled_past
    assert not set(answer.recalled) & set(answer.recalled_past)


async def test_only_the_reply_that_ends_the_turn_carries_the_recall(session_factory, seeded, chat):
    loop = make_loop(
        session_factory,
        ScriptedLLM(tool_call("lookup"), reply("Porto.")),
        tools=FakeTools({"lookup": "found"}),
    )

    events = await play(loop, chat, "Where does the owner live? Porto")

    _, asked, _, answer = await stored(session_factory, chat)
    assert asked.tool_calls and asked.recalled is None and asked.recalled_past is None
    assert answer.recalled == ids(events[0].current)


async def test_with_recall_off_the_reply_records_nothing(session_factory, seeded, chat):
    await play(
        make_loop(session_factory, ScriptedLLM(reply("I don't know.")), recall_enabled=False),
        chat,
        "Where does the owner live? Porto",
    )

    answer = (await stored(session_factory, chat))[-1]
    assert answer.recalled == () and answer.recalled_past == ()


async def test_a_turn_that_recalls_nothing_records_an_empty_list_not_that_it_was_not_recorded(
    session_factory, chat
):
    # No memories are seeded, so recall finds nothing: "nothing matched" is a fact worth keeping,
    # and it must stay apart from "not recorded", which is what an old reply has
    events = await play(
        make_loop(session_factory, ScriptedLLM(reply("I do not know."))), chat, "anything at all"
    )

    answer = (await stored(session_factory, chat))[-1]
    assert events[0].current == () and events[0].past == ()
    assert answer.recalled == () and answer.recalled_past == ()
