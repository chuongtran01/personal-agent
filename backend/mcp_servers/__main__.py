"""Run an MCP server as its own process:

    python -m mcp_servers email|calendar

It reads MCP_SIGNING_SECRET (the secret the agent signs its tokens with, at least 32 characters),
MCP_PROVIDER (``fake`` for the made-up mailbox and calendar, the default) and MCP_PORT, and listens
on 127.0.0.1 only. One worker: the memory of tokens already used is per process.
"""

import sys

import uvicorn
from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp_servers import calendar, email
from mcp_servers.auth import MIN_SECRET_LENGTH
from mcp_servers.providers.fake import FakeCalendarProvider, FakeMailProvider

PORTS = {"email": 8101, "calendar": 8102}


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    mcp_signing_secret: str = ""
    mcp_provider: str = "fake"
    mcp_port: int = 0


def build(name: str, settings: ServerSettings):
    """The guarded ASGI app for one server, or raises SystemExit with what is wrong."""
    if len(settings.mcp_signing_secret) < MIN_SECRET_LENGTH:
        raise SystemExit(f"MCP_SIGNING_SECRET must be at least {MIN_SECRET_LENGTH} characters")
    if settings.mcp_provider != "fake":
        raise SystemExit(
            f"unknown MCP_PROVIDER {settings.mcp_provider!r}; only 'fake' exists so far"
        )
    if name == "email":
        return email.create_app(FakeMailProvider(), settings.mcp_signing_secret)
    return calendar.create_app(FakeCalendarProvider(), settings.mcp_signing_secret)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in PORTS:
        print("usage: python -m mcp_servers email|calendar", file=sys.stderr)
        return 2
    settings = ServerSettings()
    app = build(argv[0], settings)
    uvicorn.run(app, host="127.0.0.1", port=settings.mcp_port or PORTS[argv[0]], workers=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
