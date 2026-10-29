"""What the agent remembers before a model call: the layer between the conversation and ``search()``.

Recall builds a query from the owner's last messages, asks ``search()`` for the memories that may be
used now and, when the message looks back in time, once more for the past (replaced facts and ended
events). It never writes. The agent loop calls ``mark_used`` for the memories that really went into
the prompt, so ``last_used_at`` means "the model saw this".
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.memory.embeddings import Embedder
from app.memory.search import Mode, SearchResult, search
from app.models import Memory

# The latest message and the one before it, so "and her birthday?" still finds the person
QUERY_MESSAGES = 2

# Words that turn a question towards the past. A miss costs a past answer ("I don't know" about a
# replaced fact); a false hit costs a few extra lines in the prompt, marked as past, so the list
# leans towards matching. The agent eval's history cases show what it misses.
_PAST_CUES = re.compile(
    r"""
    \b(
        before | previously | formerly | originally | earlier | ago | back\s+(then|when)
        | in\s+the\s+past | used?\s+to | no\s+longer | any\s*more | ever
        | (when|where|what|who|which)\s+(was|were|did)
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Recalled:
    """``current`` may be used as true now. ``past`` is no longer true, or already happened."""

    current: tuple[SearchResult, ...]
    past: tuple[SearchResult, ...] = ()

    @property
    def memory_ids(self) -> list[int]:
        return [memory.memory_id for memory in (*self.current, *self.past)]


class _EmbedOnce:
    """Embeds the query once for the two searches of a turn."""

    def __init__(self, embedder: Embedder):
        self._embedder = embedder
        self._cache: dict[str, list[float]] = {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        missing = [text for text in dict.fromkeys(texts) if text not in self._cache]
        if missing:
            self._cache.update(zip(missing, await self._embedder.embed(missing), strict=True))
        return [self._cache[text] for text in texts]


def build_query(owner_messages: Sequence[str]) -> str:
    """The recall query: the last owner messages, oldest first."""
    return "\n".join(message.strip() for message in owner_messages[-QUERY_MESSAGES:])


def asks_about_the_past(message: str) -> bool:
    """Whether the owner's latest message looks back in time ("before", "used to", "when was")."""
    return _PAST_CUES.search(message) is not None


async def recall(
    session: AsyncSession,
    owner_messages: Sequence[str],
    *,
    now: datetime,
    embedder: Embedder,
    mode: Mode | None = None,
    limit: int | None = None,
    history_limit: int | None = None,
) -> Recalled:
    """The memories for the next model call. ``owner_messages`` is oldest first and its last item
    is the message being answered."""
    if not owner_messages or not owner_messages[-1].strip():
        return Recalled(current=())
    query = build_query(owner_messages)
    mode = mode or settings.memory_search_mode
    embedder = _EmbedOnce(embedder)
    current = await search(
        session,
        query,
        now=now,
        mode=mode,
        limit=settings.recall_limit if limit is None else limit,
        embedder=embedder,
    )
    past: list[SearchResult] = []
    if asks_about_the_past(owner_messages[-1]):
        past = await search(
            session,
            query,
            now=now,
            mode=mode,
            limit=settings.recall_history_limit if history_limit is None else history_limit,
            embedder=embedder,
            history=True,
        )
    return Recalled(current=tuple(current), past=tuple(past))


async def mark_used(session: AsyncSession, memory_ids: Sequence[int], now: datetime) -> None:
    """Record that these memories went into a prompt. Commits its own short transaction."""
    if not memory_ids:
        return
    await session.execute(update(Memory).where(Memory.id.in_(memory_ids)).values(last_used_at=now))
    await session.commit()
