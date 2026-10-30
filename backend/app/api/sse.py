"""Server-sent events for a turn.

The turn runs in a background task that writes its events to a queue, and the response reads the
queue. A client that closes the connection therefore does not stop the turn: the answer is still
stored, and the chat is given back when the turn ends, however the client behaved.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import BaseModel

from app.agent.llm import TextDelta
from app.agent.loop import AgentDone, AgentEvent, MemoryRecalled, Turn, TurnError
from app.memory.search import SearchResult

log = logging.getLogger(__name__)


class MemoryRef(BaseModel):
    id: int
    kind: str
    content: str
    valid_from: datetime | None
    valid_until: datetime | None


class MemoryPayload(BaseModel):
    current: list[MemoryRef]
    past: list[MemoryRef]


class TextPayload(BaseModel):
    text: str


class DonePayload(BaseModel):
    message_id: int
    input_tokens: int
    output_tokens: int
    steps: int
    recall_seconds: float
    total_seconds: float


class ErrorPayload(BaseModel):
    code: str
    message: str


def _ref(result: SearchResult) -> MemoryRef:
    return MemoryRef(
        id=result.memory_id,
        kind=result.kind,
        content=result.content,
        valid_from=result.valid_from,
        valid_until=result.valid_until,
    )


def encode(event: AgentEvent) -> str:
    """One event as an SSE frame. The JSON holds no raw newline, so one ``data`` line is enough."""
    match event:
        case MemoryRecalled():
            name = "memory"
            data = MemoryPayload(
                current=[_ref(r) for r in event.current], past=[_ref(r) for r in event.past]
            )
        case TextDelta():
            name, data = "text", TextPayload(text=event.text)
        case AgentDone():
            name = "done"
            data = DonePayload(
                message_id=event.message_id,
                input_tokens=event.usage.input_tokens,
                output_tokens=event.usage.output_tokens,
                steps=event.steps,
                recall_seconds=event.recall_seconds,
                total_seconds=event.total_seconds,
            )
        case TurnError():
            name, data = "error", ErrorPayload(code=event.code, message=event.message)
    return f"event: {name}\ndata: {data.model_dump_json()}\n\n"


async def _pump(turn: Turn, queue: asyncio.Queue[AgentEvent | None]) -> None:
    try:
        async for event in turn.events():
            queue.put_nowait(event)
    except Exception:
        log.exception("turn in chat %s failed", turn.session_id)
        queue.put_nowait(TurnError("internal_error", "something went wrong; please try again"))
    finally:
        turn.close()  # a no-op after events() has ended; frees the chat if the task never ran
        queue.put_nowait(None)


def run_in_background(turn: Turn, tasks: set[asyncio.Task]) -> asyncio.Queue[AgentEvent | None]:
    """Start the turn and return the queue its events arrive on, ended by None.

    ``tasks`` keeps a reference to the task, which asyncio does not."""
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    task = asyncio.create_task(_pump(turn, queue))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return queue


async def frames(queue: asyncio.Queue[AgentEvent | None]) -> AsyncIterator[str]:
    while (event := await queue.get()) is not None:
        yield encode(event)
