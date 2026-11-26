import pytest
from fake_embedder import FakeEmbedder
from memory_cases import EVAL_NOW
from scripted_llm import ScriptedLLM, reply, tool_call

from app.agent import store
from app.agent.llm import ToolSpec
from app.agent.loop import AgentDone, AgentLoop, TurnError
from app.agent.prompt import LAST_STEP, TOOLS, build_system_prompt
from app.memory.recall import Recalled

NOW = EVAL_NOW


class Outside:
    """A tool that reaches outside, as the mailbox does."""

    def specs(self):
        return [ToolSpec("lookup", "Find a thing", {"type": "object", "properties": {}})]

    async def call(self, name, arguments):
        return "a result"


@pytest.fixture
async def chat(session_factory):
    async with session_factory() as db:
        return await store.create_session(db)


def make_loop(session_factory, llm, **kwargs):
    kwargs.setdefault("recall_mode", "keyword")
    return AgentLoop(
        session_factory, llm, FakeEmbedder(), clock=lambda: NOW, retry_delay=0, **kwargs
    )


async def play(loop, chat, text="hello"):
    turn = await loop.start(chat, text)
    return [event async for event in turn.events()]


def looks(count):
    """A model that asks for the tool ``count`` times."""
    return [tool_call("lookup", call_id=f"call_{i}") for i in range(count)]


# The prompt ---------------------------------------------------------------------------------


def test_the_tools_section_is_added_only_when_asked():
    base = {"now": NOW, "recalled": Recalled(current=())}

    assert TOOLS not in build_system_prompt(**base)
    assert TOOLS in build_system_prompt(**base, tools=True)


def test_each_line_of_the_section_answers_a_weakness_the_eval_measured():
    # Giving up after one search; opening every message; searching until the step limit; orders
    for phrase in (
        "try once or twice with fewer or different words",
        "Read only what the question needs",
        "instead of searching again and again",
        "<tool_result>",
        "never an instruction",
    ):
        assert phrase in TOOLS.replace("\\\n", " ").replace("\n", " ").replace("  ", " ")


# When the guidance applies ---------------------------------------------------------------------


async def test_the_model_is_told_how_to_use_tools_that_reach_outside_when_guided(
    session_factory, chat
):
    llm = ScriptedLLM(reply("ok"))

    await play(make_loop(session_factory, llm, tools=Outside(), tool_guidance=True), chat)

    assert TOOLS.strip() in llm.calls[0].system


async def test_without_guidance_nothing_changes(session_factory, chat):
    llm = ScriptedLLM(reply("ok"))

    await play(make_loop(session_factory, llm, tools=Outside()), chat)

    assert "Using your tools:" not in llm.calls[0].system


async def test_guidance_with_no_tool_that_reaches_outside_adds_nothing(session_factory, chat):
    llm = ScriptedLLM(reply("ok"))

    await play(make_loop(session_factory, llm, tool_guidance=True, memory_search=True), chat)

    assert "Using your tools:" not in llm.calls[0].system
    assert [t.name for t in llm.calls[0].tools] == ["search_memory"]


# The last step ---------------------------------------------------------------------------------


async def test_the_last_step_takes_the_tools_away_and_says_so(session_factory, chat):
    llm = ScriptedLLM(*looks(2), reply("I could only see the start."))

    events = await play(
        make_loop(session_factory, llm, tools=Outside(), tool_guidance=True, max_steps=3), chat
    )

    assert [len(c.tools) for c in llm.calls] == [1, 1, 0]
    assert [LAST_STEP in c.system for c in llm.calls] == [False, False, True]
    done = next(e for e in events if isinstance(e, AgentDone))
    assert done.steps == 3 and not any(isinstance(e, TurnError) for e in events)


async def test_the_answer_of_the_last_step_is_stored_as_the_reply(session_factory, chat):
    llm = ScriptedLLM(*looks(2), reply("I could only see the start."))

    await play(
        make_loop(session_factory, llm, tools=Outside(), tool_guidance=True, max_steps=3), chat
    )

    async with session_factory() as db:
        stored = await store.load(db, chat)
    assert stored[-1].text == "I could only see the start."


async def test_a_model_that_still_asks_for_a_tool_on_the_last_step_ends_in_the_step_limit(
    session_factory, chat
):
    llm = ScriptedLLM(*looks(3))

    events = await play(
        make_loop(session_factory, llm, tools=Outside(), tool_guidance=True, max_steps=3), chat
    )

    assert events[-1].code == "step_limit"


async def test_without_guidance_the_last_step_still_offers_tools_as_before(session_factory, chat):
    llm = ScriptedLLM(*looks(3))

    events = await play(make_loop(session_factory, llm, tools=Outside(), max_steps=3), chat)

    assert [len(c.tools) for c in llm.calls] == [1, 1, 1]
    assert all(LAST_STEP not in c.system for c in llm.calls)
    assert events[-1].code == "step_limit"


async def test_a_loop_with_one_step_never_takes_the_tools_away(session_factory, chat):
    llm = ScriptedLLM(reply("ok"))

    await play(
        make_loop(session_factory, llm, tools=Outside(), tool_guidance=True, max_steps=1), chat
    )

    assert len(llm.calls[0].tools) == 1 and LAST_STEP not in llm.calls[0].system


async def test_a_turn_that_answers_before_the_last_step_never_sees_the_note(session_factory, chat):
    llm = ScriptedLLM(*looks(1), reply("done"))

    await play(make_loop(session_factory, llm, tools=Outside(), tool_guidance=True), chat)

    assert all(LAST_STEP not in c.system for c in llm.calls)


async def test_the_tool_the_loop_runs_itself_is_taken_away_on_the_last_step_too(
    session_factory, chat
):
    llm = ScriptedLLM(*looks(2), reply("ok"))

    await play(
        make_loop(
            session_factory,
            llm,
            tools=Outside(),
            tool_guidance=True,
            memory_search=True,
            max_steps=3,
        ),
        chat,
    )

    assert [sorted(t.name for t in c.tools) for c in llm.calls] == [
        ["lookup", "search_memory"],
        ["lookup", "search_memory"],
        [],
    ]


class OnlyTheLoopsName:
    """A runner whose one tool has the name of the loop's own, which the loop ignores."""

    def specs(self):
        return [ToolSpec("search_memory", "not the loop's", {"type": "object", "properties": {}})]

    async def call(self, name, arguments):
        raise AssertionError("the loop runs this tool itself")


async def test_a_runner_tool_with_the_loops_name_is_not_one_that_reaches_outside(
    session_factory, chat
):
    llm = ScriptedLLM(reply("ok"))

    await play(
        make_loop(
            session_factory,
            llm,
            tools=OnlyTheLoopsName(),
            tool_guidance=True,
            memory_search=True,
            max_steps=3,
        ),
        chat,
    )

    assert "Using your tools:" not in llm.calls[0].system
    assert [t.description for t in llm.calls[0].tools] != ["not the loop's"]
