import pytest
from fastapi.testclient import TestClient

from app.agent.build import build_agent
from app.config import settings
from app.main import create_app


@pytest.fixture(autouse=True)
def a_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")


def test_the_server_agent_writes_memories_by_default():
    agent = build_agent()

    assert agent.memory_writer is not None and agent.recall_enabled


def test_writing_can_be_switched_off_while_recall_stays_on(monkeypatch):
    monkeypatch.setattr(settings, "memory_writes_enabled", False)

    agent = build_agent()

    assert agent.memory_writer is None and agent.recall_enabled


def test_the_writer_uses_the_extraction_model_when_one_is_set(monkeypatch):
    monkeypatch.setattr(settings, "extraction_model", "small-model")
    monkeypatch.setattr(settings, "agent_model", "chat-model")

    agent = build_agent()

    assert agent.memory_writer._llm._model == "small-model"
    assert agent._llm._model == "chat-model"


def test_the_writer_falls_back_to_the_chat_model(monkeypatch):
    monkeypatch.setattr(settings, "extraction_model", "")
    monkeypatch.setattr(settings, "agent_model", "chat-model")

    assert build_agent().memory_writer._llm._model == "chat-model"


def test_without_a_key_the_agent_cannot_be_built(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_agent()


def test_shutting_the_server_down_closes_the_agent_so_queued_writes_can_finish():
    class Agent:
        closed = False

        async def close(self):
            self.closed = True

    app = create_app(docs=False)
    agent = Agent()

    with TestClient(app):
        app.state.agent = agent
        assert not agent.closed

    assert agent.closed


def test_shutting_down_a_server_whose_agent_was_never_built_is_fine():
    app = create_app(docs=False)

    with TestClient(app):
        assert app.state.agent is None
