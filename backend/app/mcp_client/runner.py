"""The agent's MCP client, as the loop's ``ToolRunner`` (docs/PHASE6.md).

It finds out what each server offers, and runs a tool by sending the server one request with a
token made for that request. It holds no provider credential: only the secret that signs the
tokens. What comes back is untrusted text from outside, so it is wrapped and labelled as data
before the model sees it; what goes wrong never raises into the loop and never carries a URL, a
stack or a provider's words, only a fixed phrase the model can read.

A read is tried once more after a lost connection or a timeout. A refusal is not retried, and nor
is any tool a server does not mark read-only, so a write added later is never repeated here.
"""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.agent.llm import ToolSpec
from app.mcp_client.tokens import SESSION_SCOPE, mint

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
RETRY_DELAY_SECONDS = 0.3
# What the model is shown of one result, whatever a server returned
MAX_RESULT_CHARS = 20_000

OPENING = '<tool_result tool="{tool}" source="external">'
CLOSING = "</tool_result>"

# The words a failure is reported in. They say what happened and nothing else
PHRASES = {
    "down": "error: the {} could not be reached",
    "slow": "error: the {} did not answer in time",
    "refused": "error: the {} refused the request",
    "failed": "error: the {} returned an error",
}


@dataclass(frozen=True)
class McpServer:
    """One server: ``service`` is how a failure names it ("mailbox"), ``audience`` is what its
    tokens are made for."""

    service: str
    url: str
    audience: str


def wrap(tool: str, text: str) -> str:
    """Outside text as data: labelled, cut at a length, and unable to close its own label."""
    if len(text) > MAX_RESULT_CHARS:
        text = (
            text[:MAX_RESULT_CHARS]
            + f"\n[cut here: the result is over {MAX_RESULT_CHARS} characters]"
        )
    # A result cannot close the label early and pass the rest off as the assistant's own words
    text = text.replace(CLOSING, "&lt;/tool_result>").replace("</Tool_Result", "&lt;/Tool_Result")
    return f"{OPENING.format(tool=tool)}\n{text}\n{CLOSING}"


class TokenAuth(httpx2.Auth):
    """Signs every request with a token made for it: for the tool, when the request calls one, and
    for the session otherwise (finding out what the server offers).

    It also notes when a server answered 401 or 403. The MCP client turns any non-2xx answer into
    one generic error without the status, so this is the only place a refusal can be told from a
    server that failed."""

    def __init__(self, secret: str, audience: str):
        self._secret = secret
        self._audience = audience
        self.refused = False

    def auth_flow(self, request: httpx2.Request):
        request.headers["Authorization"] = "Bearer " + mint(
            self._secret, audience=self._audience, scope=scope_of(request)
        )
        response = yield request
        if response.status_code in (401, 403):
            self.refused = True


def scope_of(request: httpx2.Request) -> str:
    """The tool a request calls, or ``session`` for anything else a connection says."""
    try:
        message = json.loads(request.content)
    except (ValueError, TypeError, httpx2.RequestNotRead):
        return SESSION_SCOPE
    if isinstance(message, dict) and message.get("method") == "tools/call":
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        if isinstance(name, str) and name:
            return name
    return SESSION_SCOPE


def _leaves(error: BaseException) -> list[BaseException]:
    """The errors inside (possibly nested) exception groups, which is how anyio reports a failure."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaves(inner)]
    return [error]


def classify(error: BaseException) -> str:
    """Sort a failure into down, slow or failed. A refusal is noticed by ``TokenAuth``, not here."""
    leaves = _leaves(error)
    for leaf in leaves:
        if isinstance(leaf, TimeoutError | httpx2.TimeoutException):
            return "slow"
    for leaf in leaves:
        if isinstance(leaf, httpx2.TransportError | ConnectionError | OSError):
            return "down"
    return "failed"


class McpToolRunner:
    """Implements ``ToolRunner``: ``specs`` is what ``connect`` found, ``call`` runs one tool."""

    def __init__(
        self,
        servers: Sequence[McpServer],
        *,
        secret: str,
        timeout: float = TIMEOUT_SECONDS,
        retry_delay: float = RETRY_DELAY_SECONDS,
        transports: Mapping[str, httpx2.AsyncBaseTransport] | None = None,
    ):
        self._servers = list(servers)
        self._secret = secret
        self._timeout = timeout
        self._retry_delay = retry_delay
        self._transports = dict(transports or {})
        self._specs: list[ToolSpec] = []
        self._owner: dict[str, McpServer] = {}
        self._read_only: set[str] = set()

    def specs(self) -> Sequence[ToolSpec]:
        return tuple(self._specs)

    async def connect(self) -> None:
        """Ask every server what it offers. A server that cannot answer is logged and has no tools
        until the next ``connect``, so one server being down does not take the other's tools."""
        specs: list[ToolSpec] = []
        owner: dict[str, McpServer] = {}
        read_only: set[str] = set()
        for server in self._servers:
            conn = self._client(server)
            try:
                with anyio.fail_after(self._timeout):
                    async with conn as client:
                        tools = (await client.list_tools()).tools
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError | KeyboardInterrupt | SystemExit):
                    raise
                log.warning(
                    "%s: could not list tools (%s)", server.service, failure_of(error, conn)
                )
                continue
            for tool in tools:
                if tool.name in owner:
                    log.warning(
                        "%s: tool %s is already offered, skipped", server.service, tool.name
                    )
                    continue
                specs.append(ToolSpec(tool.name, tool.description or "", dict(tool.input_schema)))
                owner[tool.name] = server
                if tool.annotations and tool.annotations.read_only_hint:
                    read_only.add(tool.name)
        self._specs, self._owner, self._read_only = specs, owner, read_only

    def _client(self, server: McpServer) -> "_Connection":
        auth = TokenAuth(self._secret, server.audience)
        http = httpx2.AsyncClient(
            auth=auth,
            timeout=self._timeout,
            transport=self._transports.get(server.service),
            base_url=server.url,
        )
        return _Connection(http, server.url, auth)

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        server = self._owner.get(name)
        if server is None:
            raise KeyError(name)  # the loop says there is no such tool
        attempts = 2 if name in self._read_only else 1
        failure = "failed"
        for attempt in range(attempts):
            conn = self._client(server)
            try:
                with anyio.fail_after(self._timeout):
                    async with conn as client:
                        result = await client.call_tool(name, arguments)
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError | KeyboardInterrupt | SystemExit):
                    raise
                failure = failure_of(error, conn)
                log.warning("%s: %s failed (%s)", server.service, name, failure)
                if failure in ("down", "slow") and attempt + 1 < attempts:
                    await asyncio.sleep(self._retry_delay)
                    continue
                break
            if result.is_error:
                log.warning("%s: %s returned an error result", server.service, name)
                return PHRASES["failed"].format(server.service)
            text = "\n".join(b.text for b in result.content if getattr(b, "type", "") == "text")
            return wrap(name, text)
        return PHRASES[failure].format(server.service)


def failure_of(error: BaseException, connection: "_Connection") -> str:
    """Why a request failed: refused if the server answered 401 or 403, else down, slow or failed."""
    return "refused" if connection.refused else classify(error)


class _Connection:
    """A ``Client`` over one pooled HTTP connection, closed together with it."""

    def __init__(self, http: httpx2.AsyncClient, url: str, auth: TokenAuth):
        self._http = http
        self._auth = auth
        self._client = Client(streamable_http_client(url, http_client=http))

    @property
    def refused(self) -> bool:
        return self._auth.refused

    async def __aenter__(self) -> Client:
        await self._http.__aenter__()
        try:
            return await self._client.__aenter__()
        except BaseException:
            await self._http.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *exc) -> None:
        try:
            await self._client.__aexit__(*exc)
        finally:
            await self._http.__aexit__(*exc)
