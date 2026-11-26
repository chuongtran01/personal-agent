"""The agent as the server runs it. Tests and the eval runner build their own with fakes."""

from app.agent.loop import AgentLoop, ToolRunner
from app.agent.openai_client import OpenAIClient
from app.config import settings
from app.db import SessionLocal
from app.mcp_client.runner import McpServer, McpToolRunner
from app.mcp_client.tokens import MIN_SECRET_LENGTH
from app.memory.embeddings import OpenAIEmbedder
from app.memory.writer import MemoryWriter

# What each server's tokens are made for, and what a failure calls it; the servers' own constants
EMAIL = ("mailbox", "mcp-email")
CALENDAR = ("calendar", "mcp-calendar")


def build_tools() -> McpToolRunner | None:
    """The MCP client for the servers that are configured, or None when none is.

    Raises RuntimeError when a server is configured and the signing secret is missing or short:
    that is a mistake to stop on, not a reason for the agent to quietly have no tools. The caller
    runs ``connect()``, which is async."""
    servers = [
        McpServer(service, url, audience)
        for (service, audience), url in (
            (EMAIL, settings.mcp_email_url),
            (CALENDAR, settings.mcp_calendar_url),
        )
        if url
    ]
    if not servers:
        return None
    if len(settings.mcp_signing_secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"MCP_SIGNING_SECRET must be at least {MIN_SECRET_LENGTH} characters "
            "when an MCP server is configured"
        )
    return McpToolRunner(
        servers, secret=settings.mcp_signing_secret, timeout=settings.mcp_timeout_seconds
    )


def build_agent(tools: ToolRunner | None = None) -> AgentLoop:
    """Raises RuntimeError when OPENAI_API_KEY is not set."""
    chat = OpenAIClient()
    embedder = OpenAIEmbedder()
    writer = None
    if settings.memory_writes_enabled:
        extractor = OpenAIClient(model=settings.extraction_model or settings.agent_model)
        writer = MemoryWriter(SessionLocal, extractor, embedder)
    return AgentLoop(
        SessionLocal,
        chat,
        embedder,
        tools=tools,
        memory_search=settings.memory_search_enabled,
        memory_writer=writer,
    )
