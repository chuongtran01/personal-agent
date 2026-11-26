"""The real client and servers for the tool eval, run in process with no ports.

``real_stack`` starts the email and calendar servers on the fake providers and gives the loop an
``McpToolRunner`` in front of them: every call is a signed request, the answer is wrapped as outside
text, and a failure goes through the runner's own code. That is the path production takes, minus the
network, so a run measures what the wrapper and the guidance do to the agent.

A failing server is made at the transport, under the runner: ``down`` refuses the connection,
``slow`` never answers in time, ``refused`` answers 401. The runner has already found the tools, as
it would have before a server went down. It records every call like ``FakeTools`` does, so the same
checks apply.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx2
from tool_fixtures import Call, Failure

from app.agent.llm import ToolSpec
from app.mcp_client.runner import McpServer, McpToolRunner
from mcp_servers import calendar, email
from mcp_servers.providers.fake import FakeCalendarProvider, FakeMailProvider

SECRET = "eval-signing-secret-of-at-least-32-characters"
MAIL = McpServer("mailbox", "http://127.0.0.1:8101/mcp", email.AUDIENCE)
CALENDAR = McpServer("calendar", "http://127.0.0.1:8102/mcp", calendar.AUDIENCE)
# Long enough for a call in process, short enough that a slow server is soon given up on
TIMEOUT_SECONDS = 5.0
SLOW_TIMEOUT_SECONDS = 0.3


class Switch(httpx2.AsyncBaseTransport):
    """Passes requests to a server, or fails them the way a real outage would."""

    def __init__(self, inner: httpx2.AsyncBaseTransport):
        self._inner = inner
        self.failure: Failure | None = None

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.failure == "down":
            raise httpx2.ConnectError("nobody is listening")
        if self.failure == "slow":
            await asyncio.sleep(60)
        if self.failure == "refused":
            return httpx2.Response(401, json={"error": "invalid_token"})
        return await self._inner.handle_async_request(request)


class Recording:
    """A ``ToolRunner`` that records every call that reaches the runner, as ``FakeTools`` does."""

    def __init__(self, runner: McpToolRunner):
        self._runner = runner
        self.calls: list[Call] = []

    def specs(self) -> Sequence[ToolSpec]:
        return self._runner.specs()

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        output = await self._runner.call(name, arguments)  # an unknown tool raises, unrecorded
        self.calls.append(Call(name, dict(arguments), output))
        return output


@asynccontextmanager
async def real_stack(
    failure: Failure | None = None, hard: bool = False
) -> AsyncIterator[Recording]:
    """The tools of a case: servers up, tools discovered, then the failure (if any) switched on."""
    async with AsyncExitStack() as stack:
        mail_app = email.create_app(FakeMailProvider(hard=hard), SECRET)
        calendar_app = calendar.create_app(FakeCalendarProvider(hard=hard), SECRET)
        await stack.enter_async_context(mail_app.lifespan())
        await stack.enter_async_context(calendar_app.lifespan())
        switches = {
            "mailbox": Switch(httpx2.ASGITransport(app=mail_app)),
            "calendar": Switch(httpx2.ASGITransport(app=calendar_app)),
        }
        runner = McpToolRunner(
            [MAIL, CALENDAR],
            secret=SECRET,
            timeout=SLOW_TIMEOUT_SECONDS if failure == "slow" else TIMEOUT_SECONDS,
            retry_delay=0,
            transports=switches,
        )
        await runner.connect()
        for switch in switches.values():
            switch.failure = failure
        yield Recording(runner)
