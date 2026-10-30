import pytest
from pydantic import ValidationError

from app.agent.messages import (
    AssistantMessage,
    StoredMessageError,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    from_row,
    to_row,
)

CALL = ToolCall(id="call_1", name="lookup", arguments={"query": "dentist"})


@pytest.mark.parametrize(
    "message",
    [
        UserMessage(text="Where do I live?"),
        AssistantMessage(text="In Porto."),
        AssistantMessage(text="", tool_calls=(CALL,)),
        AssistantMessage(text="Let me check.", tool_calls=(CALL,)),
        AssistantMessage(text="In Porto.", recalled=(1, 6), recalled_past=(2,)),
        ToolResultMessage(call_id="call_1", output="12 November"),
    ],
)
def test_a_message_survives_the_database_round_trip(message):
    role, content = to_row(message)

    assert from_row(role, content) == message


def test_the_role_is_a_column_not_part_of_the_content():
    role, content = to_row(UserMessage(text="hi"))

    assert role == "user"
    assert content == {"text": "hi"}


def test_stored_content_is_plain_json():
    _, content = to_row(AssistantMessage(text="x", tool_calls=(CALL,)))

    assert content["tool_calls"] == [
        {"id": "call_1", "name": "lookup", "arguments": {"query": "dentist"}}
    ]


@pytest.mark.parametrize(
    ("role", "content"),
    [
        ("user", {"text": "hi", "extra": 1}),
        ("user", {}),
        ("assistant", {"text": "hi", "tool_calls": [{"id": "1", "name": "n"}]}),
        ("tool", {"text": "wrong shape"}),
        ("system", {"text": "not a stored role"}),
        ("user", ["not", "an", "object"]),
    ],
)
def test_a_row_that_does_not_fit_its_role_fails_to_load(role, content):
    with pytest.raises(ValueError):
        from_row(role, content)


def test_a_non_object_is_a_stored_message_error():
    with pytest.raises(StoredMessageError):
        from_row("user", "just text")


def test_messages_are_frozen():
    message = UserMessage(text="hi")

    with pytest.raises(ValidationError):
        message.text = "changed"


def test_a_reply_stored_before_recalls_were_kept_loads_with_none():
    old_row = {"text": "In Porto.", "tool_calls": []}

    assert from_row("assistant", old_row) == AssistantMessage(text="In Porto.")


def test_the_recalled_ids_are_stored_as_plain_numbers():
    _, content = to_row(AssistantMessage(text="x", recalled=(3, 4), recalled_past=(5,)))

    assert content["recalled"] == [3, 4] and content["recalled_past"] == [5]
