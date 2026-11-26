"""What a server needs from a provider. Two small interfaces, so Google, another provider or the
fake one behind the evals are interchangeable and the server code does not know which it has."""

from datetime import datetime
from typing import Protocol

from mcp_servers.models import EventPage, Message, MessagePage


class ProviderError(Exception):
    """The provider could not answer. The server says so in a fixed phrase, never with this text."""


class MailProvider(Protocol):
    async def search(
        self, *, query: str, sender: str, since: datetime | None, limit: int
    ) -> MessagePage:
        """Newest first. Every word of ``query`` must match; ``sender`` matches part of the
        sender's name or address; ``since`` keeps messages from that moment on."""
        ...

    async def read(self, message_id: str) -> Message | None: ...


class CalendarProvider(Protocol):
    async def list_events(self, *, start: datetime, end: datetime, limit: int) -> EventPage:
        """Events that overlap ``start`` to ``end``, soonest first."""
        ...
