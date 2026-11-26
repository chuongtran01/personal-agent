"""The agent loop: one owner message in, a stream of events out.

A turn saves the message, recalls memories, builds the prompt, calls the model, saves the answer
and marks the memories used. If the model asks for a tool the loop runs it and calls the model
again, up to a step limit. Tools come from a ``ToolRunner`` (the MCP client), except
``search_memory``, which the loop runs itself because what it finds has to count as recalled.

Rules the loop keeps (docs/PHASE3.md):

- No database transaction is open across a model call. Every step opens its own short session.
- A turn is not tied to a connection. ``Turn.events()`` can be pumped by a background task, and the
  turn still finishes and is saved if the reader goes away.
- One turn at a time per chat. The set of busy chats lives in this process, so there must be one
  server worker.
- A conversation in the database can always be replayed to the model: a half-finished reply is
  never stored, and a tool call is always followed by its result.

The loop imports no web framework and no model provider.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import store
from app.agent.llm import (
    Done,
    LLMClient,
    LLMError,
    TextDelta,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from app.agent.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from app.agent.prompt import LAST_STEP, build_system_prompt
from app.config import settings
from app.memory import tool as memory_tool
from app.memory.embeddings import Embedder
from app.memory.extract import ContextMessage
from app.memory.recall import Recalled, mark_used, recall
from app.memory.search import Mode, SearchResult
from app.memory.writer import MemoryWriter, WriteJob

log = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 1.0


# Why a message was refused before the turn began ---------------------------------------------


class TurnRefused(Exception):
    """The message was not accepted and nothing was stored. ``code`` is stable for clients."""

    code = "refused"


class SessionNotFound(TurnRefused):
    code = "session_not_found"


class EmptyMessage(TurnRefused):
    code = "empty_message"


class MessageTooLong(TurnRefused):
    code = "message_too_long"


class ChatFull(TurnRefused):
    code = "chat_full"


class TurnInProgress(TurnRefused):
    code = "turn_in_progress"


# What a turn reports -------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryRecalled:
    """What recall put in the prompt, sent first so a client can show it."""

    current: tuple[SearchResult, ...]
    past: tuple[SearchResult, ...]


@dataclass(frozen=True)
class ToolUsed:
    """The model is using a tool, sent as the call starts so a client can show it. Just the name:
    what a tool was asked and what it returned stay out of the stream."""

    name: str


@dataclass(frozen=True)
class AgentDone:
    message_id: int  # the final assistant message
    usage: Usage  # summed over every model call of the turn
    steps: int
    recall_seconds: float
    total_seconds: float
    memory_searches: tuple[str, ...] = ()  # what the model searched its memory for this turn


@dataclass(frozen=True)
class TurnError:
    """The turn ended without an answer. The owner's message stays saved, so they can resend."""

    code: str
    message: str


AgentEvent = MemoryRecalled | ToolUsed | TextDelta | AgentDone | TurnError


class ToolRunner(Protocol):
    def specs(self) -> Sequence[ToolSpec]: ...

    async def call(self, name: str, arguments: dict[str, Any]) -> str: ...


class NoTools:
    def specs(self) -> Sequence[ToolSpec]:
        return ()

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        raise KeyError(name)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _merge(recalled: Sequence[SearchResult], found: dict[int, SearchResult]) -> list[int]:
    """The ids of what recall found and then what the model searched for, each once."""
    return list(dict.fromkeys([*(r.memory_id for r in recalled), *found]))


def _earlier_messages(history: Sequence) -> tuple[ContextMessage, ...]:
    """The chat before this turn's owner message, as the memory writer reads it.

    Only what the owner and the assistant said in words: a tool call, and above all a tool result,
    is outside text and never reaches the extractor."""
    earlier: list[ContextMessage] = []
    for message in history[:-1]:  # the last one is this turn's owner message
        if isinstance(message, UserMessage):
            earlier.append(ContextMessage("owner", message.text))
        elif isinstance(message, AssistantMessage) and message.text and not message.tool_calls:
            earlier.append(ContextMessage("assistant", message.text))
    return tuple(earlier)


@dataclass
class Turn:
    """One accepted owner message. Read ``events()`` to the end, or call ``close()``."""

    agent: "AgentLoop"
    session_id: uuid.UUID
    owner_text: str = ""
    owner_message_id: int | None = None
    _released: bool = field(default=False, init=False)

    def close(self) -> None:
        """Give the chat back. Safe to call twice; ``events()`` calls it when it ends."""
        if not self._released:
            self._released = True
            self.agent._busy.discard(self.session_id)

    async def events(self) -> AsyncIterator[AgentEvent]:
        try:
            async for event in self.agent._run(self):
                yield event
        finally:
            self.close()


class AgentLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        embedder: Embedder,
        *,
        tools: ToolRunner | None = None,
        clock: Callable[[], datetime] = utcnow,
        recall_mode: Mode | None = None,
        recall_enabled: bool = True,
        memory_search: bool = False,
        tool_guidance: bool = False,
        memory_writer: MemoryWriter | None = None,
        max_steps: int | None = None,
        retry_delay: float = RETRY_DELAY_SECONDS,
    ):
        self._sessions = session_factory
        self._llm = llm
        self._embedder = embedder
        self._tools = tools or NoTools()
        self._clock = clock
        self._recall_mode = recall_mode
        self._recall_enabled = recall_enabled
        self._memory_search = memory_search
        self._tool_guidance = tool_guidance
        self._memory_writer = memory_writer
        self._max_steps = settings.agent_max_steps if max_steps is None else max_steps
        self._retry_delay = retry_delay
        self._busy: set[uuid.UUID] = set()

    @property
    def recall_enabled(self) -> bool:
        return self._recall_enabled

    @property
    def searches_memory(self) -> bool:
        """Whether the model is offered ``search_memory``. With recall off (the eval's control) it
        has no memory to search."""
        return self._memory_search and self._recall_enabled

    @property
    def memory_writer(self) -> MemoryWriter | None:
        return self._memory_writer

    async def close(self) -> None:
        """Let queued memory writes finish, for a server that is shutting down."""
        if self._memory_writer is not None:
            await self._memory_writer.close()

    async def start(self, session_id: uuid.UUID, text: str) -> Turn:
        """Accept an owner message: check it, take the chat's turn and store the message.

        Raises TurnRefused (and stores nothing) when the message cannot be taken."""
        text = text.strip()
        if not text:
            raise EmptyMessage("the message is empty")
        if len(text) > settings.max_message_chars:
            raise MessageTooLong(f"a message is at most {settings.max_message_chars} characters")
        if session_id in self._busy:
            raise TurnInProgress("this chat is still answering the last message")
        self._busy.add(session_id)  # no await between the check and this line
        turn = Turn(self, session_id, owner_text=text)
        try:
            async with self._sessions() as db:
                if not await store.session_exists(db, session_id):
                    raise SessionNotFound("no such chat")
                if await store.count_messages(db, session_id) >= settings.max_session_messages:
                    raise ChatFull("this chat is full; start a new one")
                turn.owner_message_id = await store.append(db, session_id, UserMessage(text=text))
        except BaseException:
            turn.close()
            raise
        return turn

    async def _run(self, turn: Turn) -> AsyncIterator[AgentEvent]:
        started = time.perf_counter()
        try:
            async with self._sessions() as db:
                history = await store.load(db, turn.session_id)
        except ValueError:
            log.exception("chat %s holds a message that does not fit its role", turn.session_id)
            yield TurnError(
                "history_unreadable", "this chat has a stored message that cannot be read"
            )
            return

        context = _earlier_messages(history)
        now = self._clock()
        recalled = Recalled(current=())
        recall_seconds = 0.0
        if self._recall_enabled:
            owner_messages = [m.text for m in history if isinstance(m, UserMessage)]
            began = time.perf_counter()
            try:
                async with self._sessions() as db:
                    recalled = await recall(
                        db, owner_messages, now=now, embedder=self._embedder, mode=self._recall_mode
                    )
            except Exception:
                # Answering without memory would look like "I don't know"; better to say it failed
                log.exception("recall failed in chat %s", turn.session_id)
                yield TurnError("recall_failed", "could not look up memory; please try again")
                return
            recall_seconds = time.perf_counter() - began
        yield MemoryRecalled(recalled.current, recalled.past)

        tools = tuple(self._tools.specs())
        # With guidance, the model is told how to use tools that reach outside, and the last step of
        # a turn takes the tools away so that a turn that could not finish still ends in an answer
        reaches_outside = self._tool_guidance and any(t.name != memory_tool.NAME for t in tools)
        system = build_system_prompt(
            now=now,
            recalled=recalled,
            owner_name=settings.owner_name,
            timezone=settings.owner_timezone,
            learns=self._memory_writer is not None,
            searches=self.searches_memory,
            tools=reaches_outside,
        )
        if self.searches_memory:
            # The loop's own tool comes first, and a runner may not offer one with the same name
            tools = (memory_tool.SPEC, *(t for t in tools if t.name != memory_tool.NAME))
        usage = Usage()
        marked = False
        found_current: dict[int, SearchResult] = {}  # what the model's own searches found
        found_past: dict[int, SearchResult] = {}
        searches: list[str] = []
        used: list[str] = []  # the names of the tools the model used, for the reply to record

        for step in range(1, self._max_steps + 1):
            text, calls = "", []
            last = reaches_outside and self._max_steps > 1 and step == self._max_steps
            try:
                async for event in self._call_model(
                    system + LAST_STEP if last else system, history, () if last else tools
                ):
                    if isinstance(event, TextDelta):
                        text += event.text
                        yield event
                    elif isinstance(event, ToolCallRequest):
                        calls.append(event.call)
                    elif isinstance(event, Done):
                        usage = Usage(
                            usage.input_tokens + event.usage.input_tokens,
                            usage.output_tokens + event.usage.output_tokens,
                        )
            except LLMError as error:
                log.warning("model call failed in chat %s: %s", turn.session_id, error)
                yield TurnError("model_error", "the model could not answer; please try again")
                return

            reply = AssistantMessage(
                text=text,
                tool_calls=tuple(calls),
                # Only the reply that ends the turn says what was recalled for it
                recalled=None if calls else tuple(_merge(recalled.current, found_current)),
                recalled_past=None if calls else tuple(_merge(recalled.past, found_past)),
                tools_used=None if calls else tuple(used),
            )
            async with self._sessions() as db:
                message_id = await store.append(db, turn.session_id, reply)
            history.append(reply)
            if not marked:
                marked = True
                await self._mark_used(recalled, now)

            if not calls:
                self._queue_memory_write(turn, text, context, now)
                yield AgentDone(
                    message_id=message_id,
                    usage=usage,
                    steps=step,
                    recall_seconds=recall_seconds,
                    total_seconds=time.perf_counter() - started,
                    memory_searches=tuple(searches),
                )
                return

            for call in calls:
                used.append(call.name)
                yield ToolUsed(call.name)
                if call.name == memory_tool.NAME and self.searches_memory:
                    searched = await memory_tool.run(
                        call.arguments,
                        sessions=self._sessions,
                        embedder=self._embedder,
                        now=now,
                        mode=self._recall_mode,
                        timezone=settings.owner_timezone,
                    )
                    output = searched.text
                    searches.append(searched.query)
                    fresh = [
                        r
                        for r in (*searched.current, *searched.past)
                        if r.memory_id not in found_current
                        and r.memory_id not in found_past
                        and r.memory_id not in recalled.memory_ids
                    ]
                    found_current.update((r.memory_id, r) for r in searched.current)
                    found_past.update((r.memory_id, r) for r in searched.past)
                    if fresh:
                        # The live view replaces what it shows with this, so it is everything
                        yield MemoryRecalled(
                            (*recalled.current, *found_current.values()),
                            (*recalled.past, *found_past.values()),
                        )
                else:
                    output = await self._run_tool(call)
                result = ToolResultMessage(call_id=call.id, output=output)
                async with self._sessions() as db:
                    await store.append(db, turn.session_id, result)
                history.append(result)

        yield TurnError("step_limit", "the agent used too many steps without an answer")

    def _queue_memory_write(
        self, turn: Turn, reply: str, context: tuple[ContextMessage, ...], now: datetime
    ) -> None:
        """Hand the finished turn to the memory writer. Never fails the turn: the answer is stored."""
        if self._memory_writer is None or turn.owner_message_id is None:
            return
        try:
            self._memory_writer.submit(
                WriteJob(turn.owner_text, turn.owner_message_id, now, reply, context)
            )
        except Exception:
            log.exception("could not queue the memory write for chat %s", turn.session_id)

    async def _call_model(
        self, system: str, history: Sequence, tools: Sequence[ToolSpec]
    ) -> AsyncIterator:
        """Stream one model call. A failure before anything was sent is retried once."""
        for attempt in (1, 2):
            sent = False
            try:
                async for event in self._llm.stream(system=system, messages=history, tools=tools):
                    sent = True
                    yield event
                return
            except LLMError as error:
                if sent or not error.retryable or attempt == 2:
                    raise
                log.info("retrying a model call after: %s", error)
                await asyncio.sleep(self._retry_delay)

    async def _run_tool(self, call: ToolCall) -> str:
        try:
            return await self._tools.call(call.name, call.arguments)
        except KeyError:
            return f"error: there is no tool called {call.name}"
        except Exception as error:
            log.exception("tool %s failed", call.name)
            return f"error: the tool failed ({type(error).__name__})"

    async def _mark_used(self, recalled: Recalled, now: datetime) -> None:
        if not recalled.memory_ids:
            return
        try:
            async with self._sessions() as db:
                await mark_used(db, recalled.memory_ids, now)
        except Exception:
            # Bookkeeping only: the answer is already stored and must not be lost to it
            log.exception("could not mark memories as used")
