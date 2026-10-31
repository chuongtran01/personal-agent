import json
from types import SimpleNamespace as NS

import httpx
import openai
import pytest
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseOutputItemDoneEvent,
    ResponseTextDeltaEvent,
)

from app.agent.llm import Done, LLMError, TextDelta, ToolCallRequest, ToolSpec, Usage
from app.agent.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from app.agent.openai_client import OpenAIClient, to_error, to_input, to_tools

REQUEST = httpx.Request("POST", "https://api.example.test/v1/responses")


# Real SDK event objects, built without validation so a test names only the fields the client reads
def delta(text):
    return ResponseTextDeltaEvent.model_construct(type="response.output_text.delta", delta=text)


def completed(input_tokens=11, output_tokens=7):
    usage = NS(input_tokens=input_tokens, output_tokens=output_tokens)
    return ResponseCompletedEvent.model_construct(
        type="response.completed", response=NS(usage=usage)
    )


def function_call(call_id="call_1", name="lookup", arguments='{"query": "dentist"}'):
    item = ResponseFunctionToolCall.model_construct(
        type="function_call", call_id=call_id, name=name, arguments=arguments
    )
    return ResponseOutputItemDoneEvent.model_construct(type="response.output_item.done", item=item)


class FakeResponses:
    def __init__(self, events, raises=None):
        self.requests = []
        self._events = events
        self._raises = raises

    async def create(self, **request):
        self.requests.append(request)
        if self._raises:
            raise self._raises
        return self._stream()

    async def _stream(self):
        for event in self._events:
            if isinstance(event, Exception):
                raise event
            yield event


def client_with(*events, raises=None):
    responses = FakeResponses(events, raises)
    return OpenAIClient(model="test-model", client=NS(responses=responses)), responses


async def collect(client, **kwargs):
    kwargs.setdefault("system", "be brief")
    kwargs.setdefault("messages", [UserMessage(text="hi")])
    return [event async for event in client.stream(**kwargs)]


async def test_text_deltas_stream_in_order_then_done_with_usage():
    client, _ = client_with(delta("Hel"), delta("lo"), completed(11, 7))

    events = await collect(client)

    assert events == [TextDelta("Hel"), TextDelta("lo"), Done(Usage(11, 7))]


async def test_the_request_is_stateless_and_carries_the_system_prompt():
    client, responses = client_with(completed())

    await collect(client, system="you are helpful")

    (request,) = responses.requests
    assert request["model"] == "test-model"
    assert request["instructions"] == "you are helpful"
    assert request["store"] is False
    assert request["stream"] is True
    assert "tools" not in request


async def test_tools_are_sent_when_given():
    client, responses = client_with(completed())
    spec = ToolSpec("lookup", "Find a thing", {"type": "object", "properties": {}})

    await collect(client, tools=[spec])

    assert responses.requests[0]["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Find a thing",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


async def test_a_function_call_becomes_a_tool_call_request():
    client, _ = client_with(function_call(), completed())

    events = await collect(client)

    assert events[0] == ToolCallRequest(
        ToolCall(id="call_1", name="lookup", arguments={"query": "dentist"})
    )
    assert isinstance(events[1], Done)


@pytest.mark.parametrize("arguments", ["not json", "[1, 2]"])
async def test_tool_arguments_that_are_not_an_object_fail_the_call(arguments):
    client, _ = client_with(function_call(arguments=arguments), completed())

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.retryable is False


async def test_a_finished_item_that_is_not_a_function_call_is_ignored():
    message = ResponseOutputItemDoneEvent.model_construct(
        type="response.output_item.done", item=NS(type="message")
    )
    client, _ = client_with(delta("hi"), message, completed())

    assert await collect(client) == [TextDelta("hi"), Done(Usage(11, 7))]


async def test_a_missing_usage_is_zero_tokens():
    client, _ = client_with(
        ResponseCompletedEvent.model_construct(type="response.completed", response=NS(usage=None))
    )

    assert await collect(client) == [Done(Usage())]


@pytest.mark.parametrize(
    ("kind", "event"),
    [
        ("response.failed", ResponseFailedEvent),
        ("response.incomplete", ResponseIncompleteEvent),
        ("error", ResponseErrorEvent),
    ],
)
async def test_a_failed_stream_raises_without_retry(kind, event):
    client, _ = client_with(delta("par"), event.model_construct(type=kind))

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.retryable is False


async def test_a_stream_that_ends_without_a_result_is_retryable():
    client, _ = client_with(delta("par"))

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.retryable is True


async def test_events_before_a_failure_are_still_delivered():
    client, _ = client_with(delta("par"), openai.APIConnectionError(request=REQUEST))
    seen = []

    with pytest.raises(LLMError):
        async for event in client.stream(system="s", messages=[UserMessage(text="hi")]):
            seen.append(event)

    assert seen == [TextDelta("par")]


async def test_a_provider_error_when_the_call_starts_is_translated():
    error = openai.RateLimitError(
        "slow down", response=httpx.Response(429, request=REQUEST), body=None
    )
    client, _ = client_with(raises=error)

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.retryable is True
    assert raised.value.__cause__ is error


def status_error(code):
    response = httpx.Response(code, request=REQUEST)
    return openai.APIStatusError("x", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (status_error(400), False),
        (status_error(401), False),
        (status_error(404), False),
        (status_error(408), True),
        (status_error(429), True),
        (status_error(500), True),
        (status_error(503), True),
        (openai.APIConnectionError(request=REQUEST), True),
        (openai.APITimeoutError(request=REQUEST), True),
        (openai.OpenAIError("something else"), False),
    ],
)
def test_provider_errors_are_sorted_into_retryable_and_not(error, retryable):
    assert to_error(error).retryable is retryable


def test_an_error_message_never_carries_provider_text():
    message = str(to_error(status_error(400)))

    assert message == "model call failed with status 400"


def test_the_conversation_becomes_responses_input():
    call = ToolCall(id="call_1", name="lookup", arguments={"query": "dentist"})
    messages = [
        UserMessage(text="When is my dentist?"),
        AssistantMessage(text="Checking.", tool_calls=(call,)),
        ToolResultMessage(call_id="call_1", output="12 November"),
        AssistantMessage(text="On 12 November."),
    ]

    items = to_input(messages)

    assert items[0] == {"role": "user", "content": "When is my dentist?"}
    assert items[1] == {"role": "assistant", "content": "Checking."}
    assert items[2]["type"] == "function_call"
    assert json.loads(items[2]["arguments"]) == {"query": "dentist"}
    assert items[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "12 November",
    }
    assert items[4] == {"role": "assistant", "content": "On 12 November."}


def test_an_assistant_turn_with_only_a_tool_call_has_no_empty_text_item():
    call = ToolCall(id="call_1", name="lookup", arguments={})

    items = to_input([AssistantMessage(text="", tool_calls=(call,))])

    assert [item.get("type") for item in items] == ["function_call"]


def test_no_tools_means_an_empty_list():
    assert to_tools([]) == []


def test_a_key_is_required_to_build_the_real_client(monkeypatch):
    monkeypatch.setattr("app.agent.openai_client.settings.openai_api_key", "")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIClient()


def test_what_was_recalled_for_a_reply_never_goes_to_the_provider():
    items = to_input(
        [
            UserMessage(text="Where do I live?"),
            AssistantMessage(text="In Porto.", recalled=(1, 6), recalled_past=(2,)),
        ]
    )

    assert items == [
        {"role": "user", "content": "Where do I live?"},
        {"role": "assistant", "content": "In Porto."},
    ]
