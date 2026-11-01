"""The agent as the server runs it. Tests and the eval runner build their own with fakes."""

from app.agent.loop import AgentLoop
from app.agent.openai_client import OpenAIClient
from app.config import settings
from app.db import SessionLocal
from app.memory.embeddings import OpenAIEmbedder
from app.memory.writer import MemoryWriter


def build_agent() -> AgentLoop:
    """Raises RuntimeError when OPENAI_API_KEY is not set."""
    chat = OpenAIClient()
    embedder = OpenAIEmbedder()
    writer = None
    if settings.memory_writes_enabled:
        extractor = OpenAIClient(model=settings.extraction_model or settings.agent_model)
        writer = MemoryWriter(SessionLocal, extractor, embedder)
    return AgentLoop(SessionLocal, chat, embedder, memory_writer=writer)
