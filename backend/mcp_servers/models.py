"""What a provider returns, whatever the provider is: a message, an event, and a page of either.

A server turns these into the text a tool returns; a provider (the fake one, Google) turns its own
data into these. Times are aware datetimes.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from datetime import date as Day
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, field_validator

# The owner's zone, for what "a day" means. One owner for now; phase 11 makes it per user
OWNER_ZONE = ZoneInfo("Europe/Lisbon")


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    from_name: str
    from_address: str
    date: datetime
    subject: str
    body: str
    filler: dict[str, int | str] | None = None
    tail: str = ""
    note: str | None = None

    @field_validator("date")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        """YAML may hand back a naive time for a ``Z`` date; the fixtures are all in UTC."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    def text(self) -> str:
        filler = str(self.filler["text"]) * int(self.filler["times"]) if self.filler else ""
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


@dataclass(frozen=True)
class MessagePage:
    """The newest matches, at most the limit asked for, and how many matched in all."""

    messages: list[Message]
    total: int


@dataclass(frozen=True)
class EventPage:
    events: list[Event]
    total: int


def parse_when(value: str, *, end: bool = False) -> tuple[datetime, bool]:
    """A date or date-time argument as an aware datetime in the owner's zone, and whether it was a
    date. A date as an ``end`` means the whole day, so it becomes the start of the next one."""
    text = value.strip()
    try:
        day = date.fromisoformat(text)
    except ValueError:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=OWNER_ZONE)
        return moment.astimezone(OWNER_ZONE), False
    first = datetime.combine(day, time(), OWNER_ZONE)
    return (first + timedelta(days=1) if end else first), True


def local(moment: datetime) -> datetime:
    return moment.astimezone(OWNER_ZONE)
