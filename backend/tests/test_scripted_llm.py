import pytest
from scripted_llm import ScriptedLLM, reply, tool_call

from app.agent.llm import Done, LLMError, TextDelta, ToolCallRequest, ToolSpec
from app.agent.messages import ToolCall, UserMessage


async def collect(llm, **kwargs):
    return [e async for e in llm.stream(system="s", messages=[UserMessage(text="hi")], **kwargs)]


async def test_a_script_plays_back_and_each_call_gets_the_next_one():
    llm = ScriptedLLM(reply("a", "b"), reply("c"))

    first = await collect(llm)
    second = await collect(llm)

    assert [e.text for e in first if isinstance(e, TextDelta)] == ["a", "b"]
    assert isinstance(first[-1], Done)
    assert [e.text for e in second if isinstance(e, TextDelta)] == ["c"]


async def test_calls_are_recorded_for_the_test_to_inspect():
    llm = ScriptedLLM(reply("a"))
    spec = ToolSpec("t", "d", {})

    await collect(llm, tools=[spec])

    (call,) = llm.calls
    assert call.system == "s"
    assert call.messages == (UserMessage(text="hi"),)
    assert call.tools == (spec,)


async def test_an_exception_in_a_script_is_raised_after_the_events_before_it():
    llm = ScriptedLLM([TextDelta("par"), LLMError("boom", retryable=True)])
    seen = []

    with pytest.raises(LLMError):
        async for event in llm.stream(system="s", messages=[]):
            seen.append(event)

    assert seen == [TextDelta("par")]


async def test_a_tool_call_script():
    llm = ScriptedLLM(tool_call("lookup", query="x"))

    events = await collect(llm)

    assert events[0] == ToolCallRequest(
        ToolCall(id="call_1", name="lookup", arguments={"query": "x"})
    )


async def test_calling_the_model_more_than_scripted_fails_loudly():
    llm = ScriptedLLM(reply("only"))
    await collect(llm)

    with pytest.raises(AssertionError, match="more often"):
        await collect(llm)
