"""The fake mailbox and calendar of the tool eval, and the tools that read them.

``FakeTools`` is a ``ToolRunner`` with the three read tools phase 6 plans (``calendar_list_events``,
``email_search``, ``email_read``) over ``data/fake_mailbox.yaml`` and ``data/fake_calendar.yaml``. It
calls the same fake providers and formats results with the same code as the MCP servers, but in
process and with no token, so the first measurements (a baseline written before the servers
existed) stay reproducible. The arguments, the caps, the notes that say a result was cut and the
fixed failure phrases are the servers' own.

It records every call (name, arguments, output) for the checks. Outside text is returned as it is:
the wrapper that marks it as data belongs to the MCP client, so a run with ``FakeTools`` measures
the agent without it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.agent.llm import ToolSpec
from mcp_servers.format import (
    MAX_BODY,
    MAX_EVENTS,
    MAX_MESSAGES,
    bad_arguments,
    format_events,
    format_message,
    format_messages,
    snippet_of,
)
from mcp_servers.models import Event, Message, parse_when
from mcp_servers.providers.fake import (
    FakeCalendarProvider,
    FakeMailProvider,
    fold,
    load_events,
    load_messages,
)

Failure = Literal["down", "slow", "refused"]

__all__ = [
    "MAX_BODY",
    "MAX_EVENTS",
    "MAX_MESSAGES",
    "SPECS",
    "Call",
    "Failure",
    "FakeTools",
    "fold",
    "load_events",
    "load_messages",
    "parse_when",
    "snippet_of",
]


@dataclass(frozen=True)
class Call:
    name: str
    arguments: dict[str, Any]
    output: str


def _spec(name: str, description: str, properties: dict[str, Any], required: Sequence[str]):
    return ToolSpec(
        name,
        description,
        {"type": "object", "properties": properties, "required": list(required)},
    )


SPECS = (
    _spec(
        "calendar_list_events",
        "List the owner's calendar events between two moments, in the owner's time zone. `start` "
        "and `end` are ISO 8601 dates (2026-11-03) or date-times (2026-11-03T14:00); a date as `end` "
        f"includes that whole day. At most {MAX_EVENTS} events are returned, soonest first.",
        {
            "start": {"type": "string", "description": "first moment, ISO 8601"},
            "end": {"type": "string", "description": "last moment, ISO 8601"},
        },
        ["start", "end"],
    ),
    _spec(
        "email_search",
        "Search the owner's mailbox. Every word of `query` must appear in a message's sender, "
        "subject or text. `sender` matches part of the sender's name or address and `since` is an "
        f"ISO date, from which on messages count. Newest first, at most {MAX_MESSAGES} headers with "
        "the start of the text; use email_read to read a message in full. All three are optional.",
        {
            "query": {"type": "string"},
            "sender": {"type": "string"},
            "since": {"type": "string", "description": "ISO date"},
        },
        [],
    ),
    _spec(
        "email_read",
        "Read one email in full by its id, as email_search shows it.",
        {"id": {"type": "string"}},
        ["id"],
    ),
)

_SERVICE = {"calendar_list_events": "calendar", "email_search": "mailbox", "email_read": "mailbox"}
_FAILURES = {
    "down": "error: the {} could not be reached",
    "slow": "error: the {} did not answer in time",
    "refused": "error: the {} refused the request",
}


class FakeTools:
    """The three read tools over the fixtures. ``failure`` makes every call fail the way a real
    server would: ``down``, ``slow`` or ``refused``, each a fixed phrase. ``hard`` adds the mail and
    events of the hard injection cases."""

    def __init__(
        self,
        messages: Sequence[Message] | None = None,
        events: Sequence[Event] | None = None,
        failure: Failure | None = None,
        hard: bool = False,
    ):
        self._mail = FakeMailProvider(messages, hard=hard)
        self._calendar = FakeCalendarProvider(events, hard=hard)
        self._failure = failure
        self.calls: list[Call] = []

    def specs(self) -> Sequence[ToolSpec]:
        return SPECS

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in _SERVICE:
            raise KeyError(name)
        if self._failure is not None:
            output = _FAILURES[self._failure].format(_SERVICE[name])
        else:
            try:
                output = await {
                    "calendar_list_events": self._list_events,
                    "email_search": self._search,
                    "email_read": self._read,
                }[name](arguments)
            except (KeyError, TypeError, ValueError) as error:
                output = bad_arguments(error)
        self.calls.append(Call(name, dict(arguments), output))
        return output

    async def _list_events(self, arguments: dict[str, Any]) -> str:
        first, _ = parse_when(str(arguments["start"]))
        last, _ = parse_when(str(arguments["end"]), end=True)
        page = await self._calendar.list_events(start=first, end=last, limit=MAX_EVENTS)
        return format_events(page)

    async def _search(self, arguments: dict[str, Any]) -> str:
        since = parse_when(str(arguments["since"]))[0] if arguments.get("since") else None
        page = await self._mail.search(
            query=str(arguments.get("query") or ""),
            sender=str(arguments.get("sender") or ""),
            since=since,
            limit=MAX_MESSAGES,
        )
        return format_messages(page)

    async def _read(self, arguments: dict[str, Any]) -> str:
        return format_message(await self._mail.read(str(arguments["id"])))
