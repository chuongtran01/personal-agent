"""A fake ``LLMClient`` that plays back a script, so the loop is tested without a model or a key."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from app.agent.llm import (
    Done,
    HistoryMessage,
    LLMEvent,
    TextDelta,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from app.agent.messages import ToolCall


@dataclass(frozen=True)
class Call:
    """What the loop sent for one model call, kept so a test can look at the prompt."""

    system: str
    messages: tuple[HistoryMessage, ...]
    tools: tuple[ToolSpec, ...]


def reply(*chunks: str, usage: Usage | None = None) -> list[LLMEvent | Exception]:
    """A reply that streams ``chunks`` and finishes."""
    return [*(TextDelta(chunk) for chunk in chunks), Done(usage or Usage(10, 5))]


def tool_call(name: str, call_id: str = "call_1", **arguments) -> list[LLMEvent | Exception]:
    """A reply that asks for one tool."""
    return [
        ToolCallRequest(ToolCall(id=call_id, name=name, arguments=arguments)),
        Done(Usage(10, 5)),
    ]


class ScriptedLLM:
    """Each call to ``stream`` plays the next script: events to yield, or an exception to raise.

    Put an exception among the events to fail part-way through a reply. Running out of scripts is
    an error, so a loop that calls the model too often fails the test.
    """

    def __init__(self, *scripts: Sequence[LLMEvent | Exception]):
        self._scripts = list(scripts)
        self.calls: list[Call] = []

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[HistoryMessage],
        tools: Sequence[ToolSpec] = (),
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append(Call(system, tuple(messages), tuple(tools)))
        if not self._scripts:
            raise AssertionError("the model was called more often than the test scripted")
        for item in self._scripts.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item
