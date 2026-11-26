"""The calendar MCP server: one read-only tool over a calendar provider (docs/PHASE6.md)."""

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

from mcp_servers.auth import TokenAuthMiddleware, TokenVerifier, require_scope
from mcp_servers.format import MAX_EVENTS, bad_arguments, format_events, provider_failed
from mcp_servers.models import parse_when
from mcp_servers.providers.base import CalendarProvider, ProviderError

# What the runner reads to know a call is safe to try again
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

AUDIENCE = "mcp-calendar"
SERVICE = "calendar"

LIST_EVENTS = (
    "List the owner's calendar events between two moments, in the owner's time zone. `start` and "
    "`end` are ISO 8601 dates (2026-11-03) or date-times (2026-11-03T14:00); a date as `end` "
    f"includes that whole day. At most {MAX_EVENTS} events are returned, soonest first."
)


def create_server(provider: CalendarProvider) -> MCPServer:
    server = MCPServer("calendar")

    @server.tool(name="calendar_list_events", description=LIST_EVENTS, annotations=READ_ONLY)
    async def calendar_list_events(
        start: Annotated[str, Field(description="first moment, ISO 8601")],
        end: Annotated[str, Field(description="last moment, ISO 8601")],
    ) -> str:
        require_scope("calendar_list_events")
        try:
            first = parse_when(start)[0]
            last = parse_when(end, end=True)[0]
        except (ValueError, TypeError) as error:
            return bad_arguments(error)
        try:
            page = await provider.list_events(start=first, end=last, limit=MAX_EVENTS)
        except ProviderError:
            return provider_failed(SERVICE)
        return format_events(page)

    return server


def create_app(provider: CalendarProvider, secret: str) -> TokenAuthMiddleware:
    """The server as an ASGI app that refuses every request without a valid token. Stateless, so a
    call is one request and carries its own token."""
    app = create_server(provider).streamable_http_app(stateless_http=True, json_response=True)
    return TokenAuthMiddleware(app, TokenVerifier(secret, AUDIENCE))
