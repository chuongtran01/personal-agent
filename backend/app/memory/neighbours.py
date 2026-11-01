"""The stored memories nearest to a text, for reconciliation (``Neighbours`` over Postgres).

Reconciliation compares a new statement or a request to forget with what is stored, so unlike
recall it cares about every memory that is still on file, not only the ones that may be shown now:
an event that has ended must still be found to be forgotten, a fact that starts later still counts
as a repeat, and an email's memory is labelled and offered like any other. What it leaves out is
what is already gone: replaced and deleted memories, and rows with no embedding. A request to
forget is the exception for replaced ones (``history=True``): "forget my old address" is about a
memory that was replaced when the owner moved. Deleted memories are never candidates.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.memory.embeddings import Embedder
from app.memory.reconcile import Candidate
from app.models import Memory


class DbNeighbours:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], embedder: Embedder):
        self._sessions = session_factory
        self._embedder = embedder

    async def nearest(self, text: str, limit: int, *, history: bool = False) -> list[Candidate]:
        statuses = ("active", "superseded") if history else ("active",)
        vector = (await self._embedder.embed([text]))[0]
        distance = Memory.embedding.cosine_distance(vector).label("distance")
        statement = (
            select(
                Memory.id,
                Memory.kind,
                Memory.content,
                Memory.created_at,
                Memory.source_kind,
                Memory.status,
                distance,
            )
            .where(Memory.status.in_(statuses), Memory.embedding.is_not(None))
            .order_by(distance, Memory.id)
            .limit(limit)
        )
        async with self._sessions() as db:
            rows = (await db.execute(statement)).all()
        return [
            Candidate(
                row.id,
                row.kind,
                row.content,
                row.created_at,
                row.source_kind,
                1 - row.distance,
                row.status,
            )
            for row in rows
        ]
