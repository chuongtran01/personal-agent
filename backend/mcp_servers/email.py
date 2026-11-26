"""The email MCP server: read-only tools over a mail provider (docs/PHASE6.md).

``create_server`` builds the tools; ``create_app`` puts the token check in front of them. Every tool
asks for its own scope first, so a token made for ``email_search`` cannot read a message.
"""

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

from mcp_servers.auth import TokenAuthMiddleware, TokenVerifier, require_scope
from mcp_servers.format import (
    MAX_MESSAGES,
    bad_arguments,
    format_message,
    format_messages,
    provider_failed,
)
from mcp_servers.models import parse_when
from mcp_servers.providers.base import MailProvider, ProviderError

# What the runner reads to know a call is safe to try again
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

AUDIENCE = "mcp-email"
SERVICE = "mailbox"

SEARCH = (
    "Search the owner's mailbox. Every word of `query` must appear in a message's sender, subject "
    "or text. `sender` matches part of the sender's name or address and `since` is an ISO date, "
    f"from which on messages count. Newest first, at most {MAX_MESSAGES} headers with the start of "
    "the text; use email_read to read a message in full. All three are optional."
)
READ = "Read one email in full by its id, as email_search shows it."


def create_server(provider: MailProvider) -> MCPServer:
    server = MCPServer("email")

    @server.tool(name="email_search", description=SEARCH, annotations=READ_ONLY)
    async def email_search(
        query: Annotated[str, Field(description="words to look for")] = "",
        sender: Annotated[str, Field(description="part of the sender's name or address")] = "",
        since: Annotated[str, Field(description="ISO date")] = "",
    ) -> str:
        require_scope("email_search")
        try:
            start = parse_when(since)[0] if since.strip() else None
        except (ValueError, TypeError) as error:
            return bad_arguments(error)
        try:
            page = await provider.search(
                query=query, sender=sender, since=start, limit=MAX_MESSAGES
            )
        except ProviderError:
            return provider_failed(SERVICE)
        return format_messages(page)

    @server.tool(name="email_read", description=READ, annotations=READ_ONLY)
    async def email_read(id: Annotated[str, Field(description="the message id")]) -> str:
        require_scope("email_read")
        try:
            return format_message(await provider.read(id))
        except ProviderError:
            return provider_failed(SERVICE)

    return server


def create_app(provider: MailProvider, secret: str) -> TokenAuthMiddleware:
    """The server as an ASGI app that refuses every request without a valid token. Stateless, so a
    call is one request and carries its own token."""
    app = create_server(provider).streamable_http_app(stateless_http=True, json_response=True)
    return TokenAuthMiddleware(app, TokenVerifier(secret, AUDIENCE))
