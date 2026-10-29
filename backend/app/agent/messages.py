"""The messages of a conversation, in the form the loop, the model and the database share.

``chat_messages`` stores a role and a JSON ``content``. Each shape below is frozen and rejects
unknown fields, so a stored row that does not fit its role fails when it is loaded and a
conversation can always be replayed to the model exactly as it was.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class StoredMessageError(ValueError):
    """A stored row does not fit its role."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolCall(_Frozen):
    id: str
    name: str
    arguments: dict[str, Any]


class UserMessage(_Frozen):
    role: Literal["user"] = "user"
    text: str


class AssistantMessage(_Frozen):
    role: Literal["assistant"] = "assistant"
    text: str
    tool_calls: tuple[ToolCall, ...] = ()


class ToolResultMessage(_Frozen):
    role: Literal["tool"] = "tool"
    call_id: str
    output: str


Message = Annotated[UserMessage | AssistantMessage | ToolResultMessage, Field(discriminator="role")]
_message = TypeAdapter(Message)


def to_row(message: UserMessage | AssistantMessage | ToolResultMessage) -> tuple[str, dict]:
    """The ``role`` and ``content`` columns for a message."""
    content = message.model_dump(mode="json", exclude={"role"})
    return message.role, content


def from_row(role: str, content: Any) -> UserMessage | AssistantMessage | ToolResultMessage:
    """A message from stored columns. Raises ValueError (StoredMessageError or a pydantic error) when the content does not fit the role."""
    if not isinstance(content, dict):
        raise StoredMessageError(f"{role} message content is not an object")
    return _message.validate_python({**content, "role": role})
