"""The servers over a real MCP connection, in process: a client asks, the server answers."""

from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_helpers import SECRET, stack
from tool_fixtures import SPECS, FakeTools

from app.mcp_client.tokens import mint
from mcp_servers import calendar, email
from mcp_servers.providers.base import ProviderError
from mcp_servers.providers.fake import FakeCalendarProvider, FakeMailProvider


class Scoped(httpx2.Auth):
    """Signs every request for one fixed scope, whatever the request is."""

    def __init__(self, scope, audience=email.AUDIENCE, secret=SECRET):
        self._args = (scope, audience, secret)

    def auth_flow(self, request):
        scope, audience, secret = self._args
        request.headers["Authorization"] = "Bearer " + mint(secret, audience=audience, scope=scope)
        yield request


async def ask(app, tool, arguments, auth, url="http://127.0.0.1:8001/mcp"):
    http = httpx2.AsyncClient(auth=auth, transport=httpx2.ASGITransport(app=app))
    async with (
        app.lifespan(),
        http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        return await client.call_tool(tool, arguments)


@pytest.fixture
def mail_app():
    return email.create_app(FakeMailProvider(), SECRET)


@pytest.fixture
def calendar_app():
    return calendar.create_app(FakeCalendarProvider(), SECRET)


# The same tools and the same text as the eval was measured with -----------------------------


async def test_the_servers_offer_the_tools_the_eval_was_written_for():
    async with stack() as (runner, _):
        await runner.connect()
        offered = {s.name: s for s in runner.specs()}

    assert set(offered) == {s.name for s in SPECS}
    for spec in SPECS:
        found = offered[spec.name]
        assert found.description == spec.description
        assert set(found.parameters["properties"]) == set(spec.parameters["properties"])
        assert set(found.parameters.get("required", [])) == set(spec.parameters["required"])


CALLS = [
    ("calendar_list_events", {"start": "2026-10-29", "end": "2026-10-29"}),
    ("calendar_list_events", {"start": "2026-10-30T14:00", "end": "2026-10-30T15:00"}),
    ("calendar_list_events", {"start": "2026-11-01", "end": "2026-11-01"}),
    ("calendar_list_events", {"start": "2026-10-01", "end": "2026-12-31"}),
    ("calendar_list_events", {"start": "tomorrow", "end": "friday"}),
    ("email_search", {"query": "electricity bill"}),
    ("email_search", {"query": "digest"}),
    ("email_search", {"sender": "Ana Costa", "since": "2026-10-01"}),
    ("email_search", {"query": "tax"}),
    ("email_search", {"since": "not a date"}),
    ("email_read", {"id": "msg-handbook"}),
    ("email_read", {"id": "msg-marta"}),
    ("email_read", {"id": "msg-nope"}),
]


@pytest.mark.parametrize(("tool", "arguments"), CALLS)
async def test_a_server_answers_exactly_as_the_eval_stand_in_does(tool, arguments):
    expected = await FakeTools().call(tool, arguments)
    async with stack() as (runner, _):
        await runner.connect()
        wrapped = await runner.call(tool, arguments)

    inner = wrapped.split("\n", 1)[1].rsplit("\n</tool_result>", 1)[0]
    assert inner == expected


# A token made for one thing runs that thing ----------------------------------------------


async def test_a_token_made_for_the_tool_runs_it(mail_app):
    result = await ask(mail_app, "email_read", {"id": "msg-dentist"}, Scoped("email_read"))

    assert not result.is_error and "Dr. Matos" in result.content[0].text


async def test_a_token_made_for_another_tool_runs_nothing(mail_app):
    result = await ask(mail_app, "email_read", {"id": "msg-dentist"}, Scoped("email_search"))

    assert result.is_error
    assert "Dr. Matos" not in result.content[0].text


async def test_a_session_token_runs_no_tool(mail_app):
    result = await ask(mail_app, "email_search", {"query": "dentist"}, Scoped("session"))

    assert result.is_error


async def test_a_token_for_the_calendar_does_not_open_the_mailbox(mail_app):
    http = httpx2.AsyncClient(
        auth=Scoped("email_search", audience=calendar.AUDIENCE),
        transport=httpx2.ASGITransport(app=mail_app),
    )
    async with mail_app.lifespan(), http:
        response = await http.post(
            "http://127.0.0.1:8001/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )

    assert response.status_code == 401


# Every request needs a token ---------------------------------------------------------------


@asynccontextmanager
async def nothing():
    yield


async def post(app, headers=None, json=None, running=False):
    """One request. A server's lifespan can run once, so a test that posts twice runs it itself."""
    transport = httpx2.ASGITransport(app=app)
    async with (
        nothing() if running else app.lifespan(),
        httpx2.AsyncClient(transport=transport) as http,
    ):
        return await http.post(
            "http://127.0.0.1:8001/mcp",
            headers=headers or {},
            json=json or {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )


async def test_a_request_with_no_token_gets_a_bare_401(mail_app):
    response = await post(mail_app)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"error": "invalid_token"}


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Bearer not-a-token", "abc"])
async def test_a_request_with_a_bad_authorization_header_gets_the_same_401(mail_app, header):
    response = await post(mail_app, {"Authorization": header})

    assert response.status_code == 401 and response.json() == {"error": "invalid_token"}


async def test_the_401_says_nothing_about_why(mail_app):
    wrong = "Bearer " + mint("z" * 40, audience=email.AUDIENCE, scope="session")
    expired = await post(mail_app, {"Authorization": wrong})

    assert expired.text == '{"error": "invalid_token"}'


async def test_a_token_cannot_be_used_twice(mail_app):
    headers = {
        "Authorization": "Bearer " + mint(SECRET, audience=email.AUDIENCE, scope="session"),
        "Accept": "application/json, text/event-stream",
    }

    async with mail_app.lifespan():
        first = await post(mail_app, headers, running=True)
        second = await post(mail_app, headers, running=True)

    assert first.status_code != 401 and second.status_code == 401


async def test_each_refusal_is_logged_as_a_short_code_never_with_the_token(mail_app, caplog):
    token = mint("z" * 40, audience=email.AUDIENCE, scope="session")

    with caplog.at_level("WARNING", logger="mcp_servers.auth"):
        async with mail_app.lifespan():
            await post(mail_app, {"Authorization": f"Bearer {token}"}, running=True)
            await post(mail_app, running=True)

    assert [r.getMessage() for r in caplog.records] == [
        "request refused: signature",
        "request refused: missing",
    ]
    assert token not in caplog.text


# The calendar server -----------------------------------------------------------------------


async def test_the_calendar_answers_for_its_own_scope(calendar_app):
    result = await ask(
        calendar_app,
        "calendar_list_events",
        {"start": "2026-10-29", "end": "2026-10-29"},
        Scoped("calendar_list_events", calendar.AUDIENCE),
        "http://127.0.0.1:8002/mcp",
    )

    assert "Sprint planning" in result.content[0].text


async def test_the_calendar_refuses_a_token_for_a_mail_tool(calendar_app):
    result = await ask(
        calendar_app,
        "calendar_list_events",
        {"start": "2026-10-29", "end": "2026-10-29"},
        Scoped("email_search", calendar.AUDIENCE),
        "http://127.0.0.1:8002/mcp",
    )

    assert result.is_error


# When the provider fails -------------------------------------------------------------------


class BrokenMail:
    async def search(self, **_):
        raise ProviderError("the upstream said: secret internal detail https://x.example/y")

    async def read(self, _):
        raise ProviderError("the upstream said: secret internal detail")


async def test_a_provider_failure_becomes_a_fixed_phrase_with_none_of_its_words():
    async with stack(mail=BrokenMail()) as (runner, _):
        await runner.connect()
        search = await runner.call("email_search", {"query": "a"})
        read = await runner.call("email_read", {"id": "x"})

    assert "error: the mailbox returned an error" in search and "internal detail" not in search
    assert "error: the mailbox returned an error" in read and "x.example" not in read
