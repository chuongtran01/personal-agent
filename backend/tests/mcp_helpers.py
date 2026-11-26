"""Both MCP servers and the agent's runner, wired in process with no network and no ports."""

from contextlib import AsyncExitStack, asynccontextmanager

import httpx2

from app.mcp_client.runner import McpServer, McpToolRunner
from mcp_servers import calendar, email
from mcp_servers.providers.fake import FakeCalendarProvider, FakeMailProvider

SECRET = "s" * 40
MAIL = McpServer("mailbox", "http://127.0.0.1:8001/mcp", email.AUDIENCE)
CALENDAR = McpServer("calendar", "http://127.0.0.1:8002/mcp", calendar.AUDIENCE)


class Recording(httpx2.AsyncBaseTransport):
    """Passes requests to an app and keeps what was sent and what was answered."""

    def __init__(self, app):
        self._inner = httpx2.ASGITransport(app=app)
        self.requests: list[httpx2.Request] = []
        self.statuses: list[int] = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        response = await self._inner.handle_async_request(request)
        self.statuses.append(response.status_code)
        return response


@asynccontextmanager
async def stack(
    mail=None, cal=None, *, secret=SECRET, server_secret=SECRET, servers=(MAIL, CALENDAR), **options
):
    """Yields ``(runner, transports)``: the servers are up, the runner has not connected yet."""
    async with AsyncExitStack() as exit_stack:
        mail_app = email.create_app(mail or FakeMailProvider(), server_secret)
        cal_app = calendar.create_app(cal or FakeCalendarProvider(), server_secret)
        await exit_stack.enter_async_context(mail_app.lifespan())
        await exit_stack.enter_async_context(cal_app.lifespan())
        transports = {"mailbox": Recording(mail_app), "calendar": Recording(cal_app)}
        runner = McpToolRunner(
            list(servers), secret=secret, transports=transports, retry_delay=0, **options
        )
        yield runner, transports
