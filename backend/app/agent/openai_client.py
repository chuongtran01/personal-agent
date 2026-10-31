"""``LLMClient`` over the OpenAI Responses API. Nothing outside this file imports the provider."""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import openai
from openai import AsyncOpenAI
from openai.types.responses import (
    FunctionToolParam,
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseInputParam,
    ResponseOutputItemDoneEvent,
    ResponseTextDeltaEvent,
)

from app.agent.llm import (
    Done,
    HistoryMessage,
    LLMError,
    LLMEvent,
    TextDelta,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from app.agent.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from app.config import settings

REQUEST_TIMEOUT_SECONDS = 60.0


def to_input(messages: Sequence[HistoryMessage]) -> ResponseInputParam:
    """The conversation as Responses API input items."""
    items: ResponseInputParam = []
    for message in messages:
        if isinstance(message, UserMessage):
            items.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            if message.text:
                items.append({"role": "assistant", "content": message.text})
            for call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    }
                )
        elif isinstance(message, ToolResultMessage):
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.call_id,
                    "output": message.output,
                }
            )
    return items


def to_tools(tools: Sequence[ToolSpec]) -> list[FunctionToolParam]:
    # The SDK types ``strict`` as required, but it is left out on purpose: how the API treats a
    # missing ``strict`` against ``null`` or ``false`` is for phase 6 to settle with a real tool
    return [
        cast(
            FunctionToolParam,
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        )
        for tool in tools
    ]


def to_error(error: Exception) -> LLMError:
    """Sort a provider failure into retryable (the network, the rate limit, a 5xx) or not."""
    if isinstance(error, openai.APIStatusError):
        retryable = error.status_code in (408, 409, 429) or error.status_code >= 500
        return LLMError(f"model call failed with status {error.status_code}", retryable=retryable)
    if isinstance(error, openai.APIConnectionError):  # includes timeouts
        return LLMError("could not reach the model", retryable=True)
    return LLMError(f"model call failed: {type(error).__name__}", retryable=False)


class OpenAIClient:
    def __init__(self, api_key: str = "", model: str = "", client: Any = None):
        if client is None:
            api_key = api_key or settings.openai_api_key
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set; add it to .env")
            # The loop owns retries, so the SDK does not add its own
            client = AsyncOpenAI(api_key=api_key, max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS)
        self._client = client
        self._model = model or settings.agent_model

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[HistoryMessage],
        tools: Sequence[ToolSpec] = (),
    ) -> AsyncIterator[LLMEvent]:
        request: dict[str, Any] = {
            "model": self._model,
            "instructions": system,
            "input": to_input(messages),
            "stream": True,
            "store": False,  # the conversation is ours; the provider keeps no copy
        }
        if tools:
            request["tools"] = to_tools(tools)
        finished = False  # read the stream to its end: stopping early leaves the connection open
        try:
            events = await self._client.responses.create(**request)
            async for event in events:
                if isinstance(event, ResponseTextDeltaEvent):
                    yield TextDelta(event.delta)
                elif isinstance(event, ResponseOutputItemDoneEvent) and isinstance(
                    event.item, ResponseFunctionToolCall
                ):
                    yield ToolCallRequest(_tool_call(event.item))
                elif isinstance(event, ResponseCompletedEvent):
                    usage = event.response.usage
                    finished = True
                    yield Done(Usage(usage.input_tokens, usage.output_tokens) if usage else Usage())
                elif isinstance(
                    event, ResponseFailedEvent | ResponseIncompleteEvent | ResponseErrorEvent
                ):
                    raise LLMError(f"the model stopped early ({event.type})", retryable=False)
        except LLMError:
            raise
        except openai.OpenAIError as error:
            raise to_error(error) from error
        if not finished:
            raise LLMError("the model stream ended without a result", retryable=True)


def _tool_call(item: ResponseFunctionToolCall) -> ToolCall:
    try:
        arguments = json.loads(item.arguments or "{}")
    except json.JSONDecodeError as error:
        raise LLMError(
            "the model sent tool arguments that are not JSON", retryable=False
        ) from error
    if not isinstance(arguments, dict):
        raise LLMError("the model sent tool arguments that are not an object", retryable=False)
    return ToolCall(id=item.call_id, name=item.name, arguments=arguments)
