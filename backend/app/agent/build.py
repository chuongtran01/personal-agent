"""The agent as the server runs it. Tests and the eval runner build their own with fakes."""

from app.agent.loop import AgentLoop
from app.agent.openai_client import OpenAIClient
from app.db import SessionLocal
from app.memory.embeddings import OpenAIEmbedder


def build_agent() -> AgentLoop:
    """Raises RuntimeError when OPENAI_API_KEY is not set."""
    return AgentLoop(SessionLocal, OpenAIClient(), OpenAIEmbedder())
