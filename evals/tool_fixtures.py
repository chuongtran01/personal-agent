"""The fake mailbox and calendar of the tool eval, and the tools that read them.

``FakeTools`` is a ``ToolRunner`` with the three read tools phase 6 plans (``calendar_list_events``,
``email_search``, ``email_read``) over ``data/fake_mailbox.yaml`` and ``data/fake_calendar.yaml``. It
stands in for the MCP servers while the eval is written first: the arguments, the caps, the notes
that say a result was cut and the fixed failure phrases are the ones the real servers must keep, so
the same cases can later be played against the real client and the fake provider.

It records every call (name, arguments, output) for the checks. Outside text is returned as it is:
the wrapper that marks it as data belongs to the router phase 6 builds, so until then an eval run
measures the agent without it.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from datetime import date as Day
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from app.agent.llm import ToolSpec

ROOT = Path(__file__).resolve().parent
MAILBOX = ROOT.parent / "data" / "fake_mailbox.yaml"
CALENDAR = ROOT.parent / "data" / "fake_calendar.yaml"

OWNER_ZONE = ZoneInfo("Europe/Lisbon")

MAX_EVENTS = 20
MAX_MESSAGES = 10
MAX_BODY = 8000
SNIPPET = 80

Failure = Literal["down", "slow", "refused"]

# Words a search ignores, so "emails from Helena about the review" is still a search for two words.
STOPWORDS = frozenset(
    {
        *("a", "an", "and", "about", "any", "are", "did", "do", "email", "emails", "for", "from"),
        *("have", "i", "in", "is", "it", "mail", "me", "message", "messages", "my", "of", "on"),
        *("or", "the", "to", "was", "what", "with"),
    }
)


def fold(text: str) -> str:
    """Lower case without accents, so a search for "Ines" finds "Inês"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    from_name: str
    from_address: str
    date: datetime
    subject: str
    body: str
    filler: dict[str, Any] | None = None
    tail: str = ""
    note: str | None = None

    @field_validator("date")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        """YAML may hand back a naive time for a ``Z`` date; the fixtures are all in UTC."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    def text(self) -> str:
        filler = self.filler["text"] * self.filler["times"] if self.filler else ""
        return self.body + filler + self.tail


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    start: datetime | None = None
    end: datetime | None = None
    date: Day | None = None
    location: str | None = None
    attendees: list[str] = []
    description: str | None = None
    note: str | None = None

    def span(self) -> tuple[datetime, datetime]:
        """Start and end as aware datetimes; an all-day event runs midnight to midnight."""
        if self.date is not None:
            first = datetime.combine(self.date, time(), OWNER_ZONE)
            return first, first + timedelta(days=1)
        assert self.start is not None and self.end is not None
        return self.start.replace(tzinfo=OWNER_ZONE), self.end.replace(tzinfo=OWNER_ZONE)


def load_messages(path: Path = MAILBOX) -> list[Message]:
    rows = yaml.safe_load(path.read_text())["messages"]
    messages = [Message.model_validate(row) for row in rows]
    ids = [m.id for m in messages]
    if len(ids) != len(set(ids)):
        raise ValueError("a message id appears twice")
    return messages


def load_events(path: Path = CALENDAR) -> list[Event]:
    rows = yaml.safe_load(path.read_text())["events"]
    events = [Event.model_validate(row) for row in rows]
    ids = [e.id for e in events]
    if len(ids) != len(set(ids)):
        raise ValueError("an event id appears twice")
    for event in events:
        if (event.date is None) == (event.start is None) or (event.start is None) != (
            event.end is None
        ):
            raise ValueError(f"{event.id}: give either `date`, or `start` and `end`")
    return events


def parse_when(value: str, *, end: bool = False) -> tuple[datetime, bool]:
    """A date or date-time argument as an aware datetime in the owner's zone, and whether it was a
    date. A date as an ``end`` means the whole day, so it becomes the start of the next one."""
    text = value.strip()
    try:
        day = Day.fromisoformat(text)
    except ValueError:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=OWNER_ZONE)
        return moment.astimezone(OWNER_ZONE), False
    first = datetime.combine(day, time(), OWNER_ZONE)
    return (first + timedelta(days=1) if end else first), True


def local(moment: datetime) -> datetime:
    return moment.astimezone(OWNER_ZONE)


def _format_event(event: Event) -> str:
    start, end = event.span()
    if event.date is not None:
        when = f"{start:%a %d %b %Y} (all day)"
    else:
        when = f"{start:%a %d %b %Y} {start:%H:%M}-{end:%H:%M}"
    parts = [event.id, when, event.title]
    if event.location:
        parts.append(f"at {event.location}")
    if event.attendees:
        parts.append("with " + ", ".join(event.attendees))
    line = " | ".join(parts)
    if event.description:
        line += f"\n  description: {event.description}"
    return line


def _snippet(message: Message) -> str:
    text = " ".join(message.text().split())
    return text[:SNIPPET] + ("..." if len(text) > SNIPPET else "")


def _format_header(message: Message) -> str:
    sender = f"{message.from_name} <{message.from_address}>"
    when = f"{local(message.date):%Y-%m-%d %H:%M}"
    return f"{message.id} | {sender} | {when} | {message.subject} | {_snippet(message)}"


def snippet_of(message: Message) -> str:
    """What a search shows of a message's text, for the checks on the cases."""
    return _snippet(message)


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
    server would: ``down``, ``slow`` or ``refused``, each a fixed phrase."""

    def __init__(
        self,
        messages: Sequence[Message] | None = None,
        events: Sequence[Event] | None = None,
        failure: Failure | None = None,
    ):
        self._messages = list(load_messages() if messages is None else messages)
        self._events = list(load_events() if events is None else events)
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
                output = {
                    "calendar_list_events": self._calendar,
                    "email_search": self._search,
                    "email_read": self._read,
                }[name](arguments)
            except (KeyError, TypeError, ValueError) as error:
                output = f"error: the arguments were not valid ({type(error).__name__})"
        self.calls.append(Call(name, dict(arguments), output))
        return output

    def _calendar(self, arguments: dict[str, Any]) -> str:
        first, _ = parse_when(str(arguments["start"]))
        last, _ = parse_when(str(arguments["end"]), end=True)
        found = sorted(
            (e for e in self._events if e.span()[0] < last and e.span()[1] > first),
            key=lambda e: e.span(),
        )
        if not found:
            return "No events in that range."
        shown = found[:MAX_EVENTS]
        lines = [_format_event(e) for e in shown]
        if len(found) > len(shown):
            lines.append(f"(showing the first {len(shown)} of {len(found)} events)")
        return "\n".join(lines)

    def _search(self, arguments: dict[str, Any]) -> str:
        words = [w for w in fold(str(arguments.get("query") or "")).split() if w not in STOPWORDS]
        sender = fold(str(arguments.get("sender") or "")).strip()
        since = parse_when(str(arguments["since"]))[0] if arguments.get("since") else None
        found = []
        for message in self._messages:
            who = fold(f"{message.from_name} {message.from_address}")
            haystack = fold(f"{who} {message.subject} {message.text()}")
            if (
                all(word in haystack for word in words)
                and sender in who
                and (since is None or message.date >= since.astimezone(UTC))
            ):
                found.append(message)
        if not found:
            return "No messages match."
        found.sort(key=lambda m: m.date, reverse=True)
        shown = found[:MAX_MESSAGES]
        lines = [_format_header(m) for m in shown]
        if len(found) > len(shown):
            lines.append(f"(showing the newest {len(shown)} of {len(found)} messages)")
        return "\n".join(lines)

    def _read(self, arguments: dict[str, Any]) -> str:
        wanted = str(arguments["id"])
        message = next((m for m in self._messages if m.id == wanted), None)
        if message is None:
            return "error: there is no message with that id"
        body = message.text()
        note = ""
        if len(body) > MAX_BODY:
            note = (
                f"\n[the message was cut here: it has {len(body)} characters and "
                f"the first {MAX_BODY} are shown]"
            )
            body = body[:MAX_BODY]
        header = (
            f"From: {message.from_name} <{message.from_address}>\n"
            f"Date: {local(message.date):%Y-%m-%d %H:%M}\nSubject: {message.subject}\n\n"
        )
        return header + (body or "(this message has no text)") + note
