"""A made-up mailbox and calendar, read from ``data/fake_mailbox.yaml`` and ``data/fake_calendar.yaml``.

This is what the servers run on in tests and evals, so no real account is ever touched. Its search
follows what a real mailbox does: every word of a query must match, accents and case do not matter,
and the newest message comes first. The fixtures are dated around the eval clock.
"""

import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml

from mcp_servers.models import Event, EventPage, Message, MessagePage

DATA = Path(__file__).resolve().parents[3] / "data"
MAILBOX = DATA / "fake_mailbox.yaml"
CALENDAR = DATA / "fake_calendar.yaml"
MAILBOX_HARD = DATA / "fake_mailbox_hard.yaml"
CALENDAR_HARD = DATA / "fake_calendar_hard.yaml"

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


def load_messages(path: Path = MAILBOX, *, hard: bool = False) -> list[Message]:
    """The fixture mailbox; with ``hard`` the extra mail of the hard injection cases as well."""
    paths = [path, MAILBOX_HARD] if hard else [path]
    rows = [row for each in paths for row in yaml.safe_load(each.read_text())["messages"]]
    messages = [Message.model_validate(row) for row in rows]
    ids = [m.id for m in messages]
    if len(ids) != len(set(ids)):
        raise ValueError("a message id appears twice")
    return messages


def load_events(path: Path = CALENDAR, *, hard: bool = False) -> list[Event]:
    """The fixture calendar; with ``hard`` the extra events of the hard injection cases as well."""
    paths = [path, CALENDAR_HARD] if hard else [path]
    rows = [row for each in paths for row in yaml.safe_load(each.read_text())["events"]]
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


class FakeMailProvider:
    def __init__(self, messages: Sequence[Message] | None = None, *, hard: bool = False):
        self._messages = list(load_messages(hard=hard) if messages is None else messages)

    async def search(
        self, *, query: str, sender: str, since: datetime | None, limit: int
    ) -> MessagePage:
        words = [w for w in fold(query).split() if w not in STOPWORDS]
        who_wanted = fold(sender).strip()
        found = []
        for message in self._messages:
            who = fold(f"{message.from_name} {message.from_address}")
            haystack = fold(f"{who} {message.subject} {message.text()}")
            if (
                all(word in haystack for word in words)
                and who_wanted in who
                and (since is None or message.date >= since.astimezone(UTC))
            ):
                found.append(message)
        found.sort(key=lambda m: m.date, reverse=True)
        return MessagePage(found[:limit], len(found))

    async def read(self, message_id: str) -> Message | None:
        return next((m for m in self._messages if m.id == message_id), None)


class FakeCalendarProvider:
    def __init__(self, events: Sequence[Event] | None = None, *, hard: bool = False):
        self._events = list(load_events(hard=hard) if events is None else events)

    async def list_events(self, *, start: datetime, end: datetime, limit: int) -> EventPage:
        found = sorted(
            (e for e in self._events if e.span()[0] < end and e.span()[1] > start),
            key=lambda e: e.span(),
        )
        return EventPage(found[:limit], len(found))
