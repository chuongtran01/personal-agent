"""The text a tool returns, and the limits on how much of a provider's answer it shows.

A result says when it was cut, so the model can say "I saw only the first ten". The phrases are
fixed: an error never carries a URL, a stack or a provider's own words.
"""

from mcp_servers.models import Event, EventPage, Message, MessagePage, local

MAX_EVENTS = 20
MAX_MESSAGES = 10
MAX_BODY = 8000
SNIPPET = 80


def bad_arguments(error: Exception) -> str:
    return f"error: the arguments were not valid ({type(error).__name__})"


def provider_failed(service: str) -> str:
    return f"error: the {service} returned an error"


def format_event(event: Event) -> str:
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


def format_events(page: EventPage) -> str:
    if not page.events:
        return "No events in that range."
    lines = [format_event(e) for e in page.events]
    if page.total > len(page.events):
        lines.append(f"(showing the first {len(page.events)} of {page.total} events)")
    return "\n".join(lines)


def snippet_of(message: Message) -> str:
    """What a search shows of a message's text."""
    text = " ".join(message.text().split())
    return text[:SNIPPET] + ("..." if len(text) > SNIPPET else "")


def format_header(message: Message) -> str:
    sender = f"{message.from_name} <{message.from_address}>"
    when = f"{local(message.date):%Y-%m-%d %H:%M}"
    return f"{message.id} | {sender} | {when} | {message.subject} | {snippet_of(message)}"


def format_messages(page: MessagePage) -> str:
    if not page.messages:
        return "No messages match."
    lines = [format_header(m) for m in page.messages]
    if page.total > len(page.messages):
        lines.append(f"(showing the newest {len(page.messages)} of {page.total} messages)")
    return "\n".join(lines)


def format_message(message: Message | None) -> str:
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
