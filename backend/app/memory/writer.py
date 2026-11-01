"""The memory write path as the server runs it: a queue, one worker, and the four steps.

The agent loop hands over a job when a reply is stored: the owner's message, the earlier messages
for context and the reply. Nothing waits for it. One worker takes the jobs in order, and each runs

    extract -> filter -> reconcile -> apply

so a second job reconciles against what the first one wrote. The queue and worker live in this
process, like the turn lock, so there must be one server worker (docs/PHASE3.md). A restart loses
what is still queued, which is a missed memory and not a wrong one.

A job that fails (the model is down, the answer is unusable) is logged and forgotten: the reply is
long gone and nothing is retried. The log names the message id and the kind of error, never the
owner's text. A full queue drops the new job with a warning, so a slow model cannot grow it
without end.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.llm import StructuredLLM, Usage
from app.config import settings
from app.memory.embeddings import Embedder
from app.memory.extract import ContextMessage, extract
from app.memory.filter import filter_proposals
from app.memory.neighbours import DbNeighbours
from app.memory.reconcile import reconcile
from app.memory.write import Applied, apply_decisions

log = logging.getLogger(__name__)

MAX_PENDING = 100
SHUTDOWN_SECONDS = 10.0


@dataclass(frozen=True)
class WriteJob:
    owner_text: str
    owner_message_id: int
    now: datetime
    reply: str | None = None
    context: tuple[ContextMessage, ...] = ()


@dataclass(frozen=True)
class WriteResult:
    proposed: int  # what the extractor proposed
    filtered: tuple[str, ...]  # the rule that dropped each secret
    dropped: int  # proposals an answer could not back
    applied: Applied
    usage: Usage


class MemoryWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: StructuredLLM,
        embedder: Embedder,
        *,
        timezone: str | None = None,
        min_similarity: float | None = None,
    ):
        self._sessions = session_factory
        self._llm = llm
        self._embedder = embedder
        self._timezone = timezone or settings.owner_timezone
        self._min_similarity = min_similarity
        self._queue: asyncio.Queue[WriteJob] = asyncio.Queue(maxsize=MAX_PENDING)
        self._task: asyncio.Task | None = None  # kept so it is not garbage collected

    def submit(self, job: WriteJob) -> None:
        """Queue a job and return at once. Must be called from inside the running event loop."""
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            log.warning(
                "the memory write queue is full; dropping the write for message %s",
                job.owner_message_id,
            )
            return
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._work())

    async def drain(self) -> None:
        """Wait until every queued job has finished. For tests and the eval."""
        await self._queue.join()

    async def close(self, timeout: float = SHUTDOWN_SECONDS) -> None:
        """Let queued jobs finish for ``timeout`` seconds, then stop the worker."""
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except TimeoutError:
            log.warning("%d memory write(s) were not finished at shutdown", self._queue.qsize() + 1)
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _work(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self.process(job)
            except Exception:
                log.exception("the memory write for message %s failed", job.owner_message_id)
            finally:
                self._queue.task_done()

    async def process(self, job: WriteJob) -> WriteResult:
        """Run one job now. Raises ``LLMError`` or a database error when a step fails."""
        found = await extract(
            self._llm,
            owner=[job.owner_text],
            now=job.now,
            timezone=self._timezone,
            context=job.context,
            reply=job.reply,
        )
        allowed = filter_proposals(found.proposals)
        usage = found.usage
        dropped = found.dropped
        applied = Applied((), (), (), 0)
        if allowed.kept:
            decided = await reconcile(
                self._llm,
                DbNeighbours(self._sessions, self._embedder),
                allowed.kept,
                owner=[job.owner_text],
                min_similarity=self._min_similarity,
            )
            usage = Usage(
                usage.input_tokens + decided.usage.input_tokens,
                usage.output_tokens + decided.usage.output_tokens,
            )
            dropped += decided.dropped
            async with self._sessions() as db:
                applied = await apply_decisions(
                    db,
                    decided.decisions,
                    embedder=self._embedder,
                    message_id=job.owner_message_id,
                    now=job.now,
                )
        log.info(
            "memory write for message %s: %d proposed, %d filtered, %d unusable, "
            "%d added, %d replaced, %d deleted, %d skipped",
            job.owner_message_id,
            len(found.proposals),
            len(allowed.dropped),
            dropped,
            len(applied.added),
            len(applied.superseded),
            len(applied.deleted),
            applied.skipped,
        )
        return WriteResult(len(found.proposals), allowed.dropped, dropped, applied, usage)
