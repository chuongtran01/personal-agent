import pytest
from fastapi.testclient import TestClient

from app.agent.build import build_agent, build_tools
from app.config import settings
from app.main import create_app
from mcp_servers.__main__ import PORTS, ServerSettings, build, main

SECRET = "w" * 40


@pytest.fixture(autouse=True)
def nothing_configured(monkeypatch):
    for name in ("mcp_email_url", "mcp_calendar_url"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "mcp_signing_secret", "")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")


# The agent's side --------------------------------------------------------------------------


def test_with_no_server_configured_there_is_no_tool_client():
    assert build_tools() is None
    assert build_agent().memory_writer is not None  # the agent is built as before


def test_a_configured_server_without_a_usable_secret_stops_start_up(monkeypatch):
    monkeypatch.setattr(settings, "mcp_email_url", "http://127.0.0.1:8101/mcp")

    with pytest.raises(RuntimeError, match="at least 32"):
        build_tools()
    monkeypatch.setattr(settings, "mcp_signing_secret", "too short")
    with pytest.raises(RuntimeError, match="at least 32"):
        build_tools()


def test_only_the_servers_with_a_url_are_used(monkeypatch):
    monkeypatch.setattr(settings, "mcp_signing_secret", SECRET)
    monkeypatch.setattr(settings, "mcp_calendar_url", "http://127.0.0.1:8102/mcp")

    runner = build_tools()

    assert [(s.service, s.audience) for s in runner._servers] == [("calendar", "mcp-calendar")]
    monkeypatch.setattr(settings, "mcp_email_url", "http://127.0.0.1:8101/mcp")
    assert [s.service for s in build_tools()._servers] == ["mailbox", "calendar"]


def test_the_audiences_the_agent_signs_for_are_the_ones_the_servers_check():
    from app.agent.build import CALENDAR, EMAIL
    from mcp_servers import calendar, email

    assert EMAIL == (email.SERVICE, email.AUDIENCE)
    assert CALENDAR == (calendar.SERVICE, calendar.AUDIENCE)


def test_the_timeout_comes_from_the_settings(monkeypatch):
    monkeypatch.setattr(settings, "mcp_signing_secret", SECRET)
    monkeypatch.setattr(settings, "mcp_email_url", "http://127.0.0.1:8101/mcp")
    monkeypatch.setattr(settings, "mcp_timeout_seconds", 3.0)

    assert build_tools()._timeout == 3.0


def test_the_agent_is_given_the_tools_it_is_built_with():
    class Tools:
        def specs(self):
            return ()

        async def call(self, name, arguments):
            raise KeyError(name)

    tools = Tools()

    assert build_agent(tools=tools)._tools is tools


def test_the_server_starts_without_tools_when_none_are_configured():
    app = create_app(docs=False)

    with TestClient(app) as client:
        assert client.get("/health").status_code in (200, 500)
        assert app.state.tools is None


def test_the_server_refuses_to_start_with_a_server_configured_and_no_secret(monkeypatch):
    monkeypatch.setattr(settings, "mcp_email_url", "http://127.0.0.1:8101/mcp")
    app = create_app(docs=False)

    with pytest.raises(RuntimeError, match="at least 32"), TestClient(app):
        pass


def test_the_server_starts_when_a_tool_server_is_down_and_has_none_of_its_tools(monkeypatch):
    monkeypatch.setattr(settings, "mcp_signing_secret", SECRET)
    monkeypatch.setattr(settings, "mcp_email_url", "http://127.0.0.1:9/mcp")  # nobody there
    monkeypatch.setattr(settings, "mcp_timeout_seconds", 2.0)
    app = create_app(docs=False)

    with TestClient(app):
        assert app.state.tools is not None and app.state.tools.specs() == ()


# The servers' side -------------------------------------------------------------------------


def test_a_server_needs_a_secret_and_a_known_provider():
    with pytest.raises(SystemExit, match="at least 32"):
        build("email", ServerSettings(mcp_signing_secret="short"))
    with pytest.raises(SystemExit, match="MCP_PROVIDER"):
        build("email", ServerSettings(mcp_signing_secret=SECRET, mcp_provider="google"))


def test_each_server_is_built_with_the_fake_provider_and_its_own_audience():
    from mcp_servers import calendar, email

    for name, module in (("email", email), ("calendar", calendar)):
        app = build(name, ServerSettings(mcp_signing_secret=SECRET))
        assert app._verifier._audience == module.AUDIENCE


def test_the_entry_point_names_the_servers_and_refuses_anything_else(capsys):
    assert set(PORTS) == {"email", "calendar"} and PORTS["email"] != PORTS["calendar"]
    assert main([]) == 2 and main(["drive"]) == 2 and main(["email", "extra"]) == 2
    assert "usage: python -m mcp_servers" in capsys.readouterr().err
