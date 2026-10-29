"""The agent's view of a language model: one streaming call, no provider in sight.

The loop talks to ``LLMClient`` and nothing else, so it is tested with a scripted fake and the
provider can change without touching it. Events come out in order: any number of text deltas, any
tool calls the model asked for, then exactly one ``Done``.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.agent.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage

HistoryMessage = UserMessage | AssistantMessage | ToolResultMessage


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. ``parameters`` is a JSON schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallRequest:
    call: ToolCall


@dataclass(frozen=True)
class Done:
    usage: Usage


LLMEvent = TextDelta | ToolCallRequest | Done


class LLMError(Exception):
    """A model call failed. ``retryable`` says whether trying the same call again can help."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    def stream(
        self,
        *,
        system: str,
        messages: Sequence[HistoryMessage],
        tools: Sequence[ToolSpec] = (),
    ) -> AsyncIterator[LLMEvent]:
        """Stream one model reply. Raises LLMError, possibly after some deltas were yielded."""
        ...
