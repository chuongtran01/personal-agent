"""Keyword, vector and hybrid search over memories, behind one function.

Keyword search ranks with Postgres full-text (``websearch_to_tsquery``, any term may match, ranked
by ``ts_rank_cd``). Vector search ranks by cosine similarity on the embeddings. Hybrid fuses the
two rankings with reciprocal rank fusion (RRF). Hybrid with recency adds a term that favours newer
memories.

Every mode reads only memories that may be used now: active, not expired, not yet to come, and
from the owner. The filter is part of the SQL, so a superseded, deleted, expired or external
memory cannot come back from any mode. ``now`` is always passed in, never read from the clock.
"""

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.memory.embeddings import Embedder
from app.models import Memory

Mode = Literal["keyword", "vector", "hybrid", "hybrid_recency"]

RRF_K = 60
CANDIDATES = 20


@dataclass(frozen=True)
class SearchResult:
    memory_id: int
    kind: str
    content: str
    source_kind: str
    valid_from: datetime | None
    valid_until: datetime | None
    created_at: datetime
    score: float
    keyword_score: float | None = None
    vector_score: float | None = None
    recency_score: float | None = None


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Fuse ranked id lists. An id scores ``1 / (k + rank)`` in each list that has it.

    Returns ``(id, score)`` best first. Ties keep the order in which ids first appear.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])


def fuse_results(
    keyword: list[SearchResult], vector: list[SearchResult], k: int = RRF_K
) -> list[SearchResult]:
    """Merge keyword and vector results into one list ordered by RRF score."""
    by_id = {r.memory_id: r for r in vector} | {r.memory_id: r for r in keyword}
    keyword_scores = {r.memory_id: r.keyword_score for r in keyword}
    vector_scores = {r.memory_id: r.vector_score for r in vector}
    fused = reciprocal_rank_fusion(
        [[r.memory_id for r in keyword], [r.memory_id for r in vector]], k
    )
    return [
        replace(
            by_id[memory_id],
            score=score,
            keyword_score=keyword_scores.get(memory_id),
            vector_score=vector_scores.get(memory_id),
        )
        for memory_id, score in fused
    ]


def recency_score(
    created_at: datetime, valid_from: datetime | None, now: datetime, half_life_days: float
) -> float:
    """1 for a memory that is current as of ``now``, halving-ish with age: ``exp(-age / half_life)``.

    Age runs from when the fact started to hold (``valid_from``, else ``created_at``). A start
    after ``now`` counts as age zero.
    """
    age_days = max((now - (valid_from or created_at)).total_seconds() / 86400, 0.0)
    return math.exp(-age_days / half_life_days)


def apply_recency(
    results: list[SearchResult], now: datetime, weight: float, half_life_days: float
) -> list[SearchResult]:
    """Add the recency term to each score and reorder. Ties keep their earlier order."""
    scored = []
    for result in results:
        recency = recency_score(result.created_at, result.valid_from, now, half_life_days)
        scored.append(replace(result, score=result.score + weight * recency, recency_score=recency))
    return sorted(scored, key=lambda r: -r.score)


def _usable(
    now: datetime,
    include_external: bool,
    include_upcoming: bool,
    include_inactive: bool = False,
    include_ended: bool = False,
) -> list:
    """The conditions a memory must meet to be returned. Each flag lifts one of them."""
    conditions = []
    if not include_inactive:
        conditions.append(Memory.status == "active")
    if not include_ended:
        conditions.append(or_(Memory.valid_until.is_(None), Memory.valid_until > now))
    if not include_upcoming:
        conditions.append(or_(Memory.valid_from.is_(None), Memory.valid_from <= now))
    if not include_external:
        conditions.append(Memory.source_kind == "owner")
    return conditions


_COLUMNS = (
    Memory.id,
    Memory.kind,
    Memory.content,
    Memory.source_kind,
    Memory.valid_from,
    Memory.valid_until,
    Memory.created_at,
)


def _result(row, score: float, **scores: float) -> SearchResult:
    return SearchResult(
        memory_id=row.id,
        kind=row.kind,
        content=row.content,
        source_kind=row.source_kind,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        created_at=row.created_at,
        score=score,
        **scores,
    )


async def keyword_search(
    session: AsyncSession, query: str, conditions: list, limit: int = CANDIDATES
) -> list[SearchResult]:
    # websearch_to_tsquery ANDs every term, so a natural-language question matches no
    # memory. Swap the ANDs for ORs and let ts_rank_cd rank memories that match more terms.
    all_terms = func.websearch_to_tsquery("english", query)
    tsquery = cast(func.replace(cast(all_terms, Text), " & ", " | "), TSQUERY)
    rank = func.ts_rank_cd(Memory.tsv, tsquery).label("score")
    statement = (
        select(*_COLUMNS, rank)
        .where(Memory.tsv.op("@@")(tsquery), *conditions)
        .order_by(rank.desc(), Memory.id)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [_result(row, row.score, keyword_score=row.score) for row in rows]


async def vector_search(
    session: AsyncSession, embedding: list[float], conditions: list, limit: int = CANDIDATES
) -> list[SearchResult]:
    distance = Memory.embedding.cosine_distance(embedding).label("distance")
    statement = (
        select(*_COLUMNS, distance)
        .where(Memory.embedding.is_not(None), *conditions)
        .order_by(distance, Memory.id)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [_result(row, 1 - row.distance, vector_score=1 - row.distance) for row in rows]


async def search(
    session: AsyncSession,
    query: str,
    *,
    now: datetime,
    mode: Mode = "hybrid_recency",
    limit: int = 5,
    embedder: Embedder | None = None,
    include_external: bool = False,
    include_upcoming: bool = False,
    include_inactive: bool = False,
    include_ended: bool = False,
    recency_weight: float | None = None,
    recency_half_life_days: float | None = None,
) -> list[SearchResult]:
    """Return the top ``limit`` usable memories for a query. Every mode but keyword needs an
    embedder. External memories, facts that start later, superseded or deleted memories and ended
    ones are left out unless asked for. The last two flags exist for the eval's negative control
    (``memory_retrieval_eval.py --control``); the agent never sets them."""
    if mode != "keyword" and embedder is None:
        raise ValueError(f"{mode} search needs an embedder")
    conditions = _usable(now, include_external, include_upcoming, include_inactive, include_ended)

    if mode == "keyword":
        return await keyword_search(session, query, conditions, limit)

    assert embedder is not None
    embedding = (await embedder.embed([query]))[0]
    if mode == "vector":
        return await vector_search(session, embedding, conditions, limit)

    keyword = await keyword_search(session, query, conditions)
    vector = await vector_search(session, embedding, conditions)
    fused = fuse_results(keyword, vector)
    if mode == "hybrid_recency":
        fused = apply_recency(
            fused,
            now,
            settings.recency_weight if recency_weight is None else recency_weight,
            settings.recency_half_life_days
            if recency_half_life_days is None
            else recency_half_life_days,
        )
    return fused[:limit]
