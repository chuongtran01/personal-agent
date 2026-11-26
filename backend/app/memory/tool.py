"""The ``search_memory`` tool: the model searches what it remembers, in its own words (docs/PHASE6.md).

Recall runs before the first model call on the owner's message alone, so a request that needs a fact
filed under other words ("a restaurant for tonight" and "vegetarian") can miss it. This tool lets
the model write the query. It is the agent's own memory, not a provider, so it runs in process.

``SPEC`` is what the model sees and ``run`` is the tool: it checks the arguments, searches with the
same code recall uses (``recall_query``), marks what it found as used because the model reads it
now, and returns the text to hand back along with what it found. The loop keeps the rest of the
bookkeeping, which needs the turn: it stores what a search found as recalled for the reply and tells
the live view.

It only reads, and it asks ``search()`` for what recall may use: owner memories that are active and
in force, never text that came from outside. With ``include_history`` it also finds what was true
once (replaced facts and events that have ended), as recall does for a question about the past.
Whatever goes wrong is a fixed phrase for the model and never ends the turn.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import ToolSpec
from app.agent.prompt import format_found
from app.memory.embeddings import Embedder
from app.memory.recall import mark_used, recall_query
from app.memory.search import Mode, SearchResult

log = logging.getLogger(__name__)

NAME = "search_memory"
MAX_QUERY_CHARS = 300

DESCRIPTION = (
    "Search what you remember about the owner, with a query you write. Use it when the memories "
    "already shown do not cover what the request depends on, for example a request for a "
    'restaurant may depend on their diet: search for "diet". Search for one thing at a time, '
    "named by what it is. Returns the best matches. With include_history it also finds what was "
    "true once and no longer is, for a question about the past."
)

SPEC = ToolSpec(
    NAME,
    DESCRIPTION,
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": 'what to look for, in a few words, such as "diet" or "sister"',
            },
            "include_history": {
                "type": "boolean",
                "description": "also find what was true once and no longer is",
            },
        },
        "required": ["query"],
    },
)

EMPTY_QUERY = "error: the query is empty"
BAD_ARGUMENTS = "error: the arguments were not valid"
FAILED = "error: the memory search failed"


@dataclass(frozen=True)
class Query:
    text: str
    include_history: bool


def parse(arguments: dict[str, Any]) -> Query | str:
    """The query, or the fixed phrase to hand the model when the arguments cannot be used."""
    text = arguments.get("query")
    history = arguments.get("include_history", False)
    if not isinstance(text, str) or not isinstance(history, bool):
        return BAD_ARGUMENTS
    text = " ".join(text.split())[:MAX_QUERY_CHARS]
    return Query(text, history) if text else EMPTY_QUERY


@dataclass(frozen=True)
class Searched:
    """What a search came to: the text for the model, what it found, and what was searched for."""

    text: str
    current: tuple[SearchResult, ...] = ()
    past: tuple[SearchResult, ...] = ()
    query: str = ""  # as searched, empty when the arguments could not be used


async def run(
    arguments: dict[str, Any],
    *,
    sessions: Callable[[], AsyncSession],
    embedder: Embedder,
    now: datetime,
    mode: Mode | None = None,
    timezone: str = "UTC",
) -> Searched:
    """Run one search for the model. Never raises: a failure is a fixed phrase it can read."""
    query = parse(arguments)
    if isinstance(query, str):
        return Searched(query)
    try:
        async with sessions() as db:
            found = await recall_query(
                db,
                query.text,
                history=query.include_history,
                now=now,
                embedder=embedder,
                mode=mode,
            )
    except Exception:
        log.exception("the memory search failed")
        return Searched(FAILED, query=query.text)
    if found.memory_ids:
        try:
            async with sessions() as db:
                await mark_used(db, found.memory_ids, now)
        except Exception:
            # Bookkeeping only: the model should still get what it asked for
            log.exception("could not mark searched memories as used")
    return Searched(
        format_found(found.current, found.past, timezone), found.current, found.past, query.text
    )
