"""The ``search_memory`` tool: the model searches what it remembers, in its own words (docs/PHASE6.md).

Recall runs before the first model call on the owner's message alone, so a request that needs a fact
filed under other words ("a restaurant for tonight" and "vegetarian") can miss it. This tool lets
the model write the query. It is the agent's own memory, not a provider, so it runs in process and
the loop itself runs it: what it finds has to count as recalled (shown under the reply, marked used)
exactly as what recall found does.

It only reads, and it asks ``search()`` for what recall may use: owner memories that are active and
in force, never text that came from outside. With ``include_history`` it also finds what was true
once (replaced facts and events that have ended), as recall does for a question about the past.
"""

from dataclasses import dataclass
from typing import Any

from app.agent.llm import ToolSpec

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
