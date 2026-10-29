"""Memory retrieval eval: recall@5, MRR and leaks for keyword, vector, hybrid and hybrid + recency.

Run from ``backend/`` (``make eval-memory-retrieval``):

    python ../evals/memory_retrieval_eval.py [--split dev|test] [--modes keyword vector ...]

The dev split is the default and is the one to tune against. The test split is held out: read its
numbers, do not tune on them. The runner uses its own database, ``agent_eval``, emptied and
re-seeded on every run, so it can never touch memories in the dev database. Vector and hybrid
modes embed the seed memories and the questions once (a fraction of a cent); keyword mode needs
no API key.

The gate is zero leaks: no memory the search may not return, and none a row forbids, in any
mode's top 10. A run with a leak exits with status 1.

``--control`` is the negative control for that gate: it lifts every filter and reports how many
forbidden memories then come back. If a question's forbidden memory never does, the question is
not a real test of the filter. The control reports and does not gate.
"""

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import get_args

import psycopg
from memory_cases import EVAL_NOW, InvalidDataset
from memory_retrieval_cases import RetrievalCase, load_all, why_not_returnable
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.memory.embeddings import OpenAIEmbedder
from app.memory.search import Mode, search
from app.memory.seed import SeedMemory, load_seed, reset, seed

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parent / "backend"
RESULTS = ROOT / "results"

SPLITS = ("dev", "test")
MODES: tuple[str, ...] = get_args(Mode)
RECALL_K = 5
DEPTH = 10  # results retrieved per question; recall uses the first RECALL_K, MRR all of them
EVAL_DATABASE = "agent_eval"


class EvalError(RuntimeError):
    """The eval cannot run (no database, no API key); not a failure of the search."""


@dataclass(frozen=True)
class CaseOutcome:
    id: str
    category: str
    expect_no_answer: bool
    first_rank: int | None  # 1-based rank of the first relevant memory
    all_found: bool  # every relevant memory is in the top RECALL_K
    leaks: list[str]  # returned memories the search may not return, or the row forbids
    top_score: float
    ranked: list[dict]


def evaluate_case(
    case: RetrievalCase,
    seed_memories: dict[str, SeedMemory],
    ranked: list[tuple[str, float]],
) -> CaseOutcome:
    """Score one question. ``ranked`` is the search result as ``(seed key, score)``, best first."""
    keys = [key for key, _ in ranked]
    relevant = set(case.relevant_keys)
    first_rank = next((rank for rank, key in enumerate(keys, 1) if key in relevant), None)
    leaks = [
        key
        for key in keys
        if key in case.forbidden_keys or why_not_returnable(seed_memories[key], case)
    ]
    return CaseOutcome(
        id=case.id,
        category=case.category,
        expect_no_answer=case.expect_no_answer,
        first_rank=first_rank,
        all_found=relevant <= set(keys[:RECALL_K]),
        leaks=leaks,
        top_score=ranked[0][1] if ranked else 0.0,
        ranked=[{"key": key, "score": score} for key, score in ranked],
    )


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _spread(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def _quality(outcomes: list[CaseOutcome]) -> dict:
    return {
        "questions": len(outcomes),
        f"recall@{RECALL_K}": _mean(
            [o.first_rank is not None and o.first_rank <= RECALL_K for o in outcomes]
        ),
        "mrr": _mean([1 / o.first_rank if o.first_rank else 0.0 for o in outcomes]),
        f"all@{RECALL_K}": _mean([o.all_found for o in outcomes]),
    }


def summarize(outcomes: list[CaseOutcome]) -> dict:
    answerable = [o for o in outcomes if not o.expect_no_answer]
    no_answer = [o for o in outcomes if o.expect_no_answer]
    return {
        **_quality(answerable),
        "leaks": sum(len(o.leaks) for o in outcomes),
        "top_score": {
            "answerable": _spread([o.top_score for o in answerable]),
            "no_answer": _spread([o.top_score for o in no_answer]),
        },
        "by_category": {
            category: _quality([o for o in answerable if o.category == category])
            for category in sorted({o.category for o in answerable})
        },
    }


def summarize_control(cases: list[RetrievalCase], outcomes: list[CaseOutcome]) -> dict[str, object]:
    """With every filter lifted, how many questions get a memory they forbid back."""
    with_forbidden = [(c, o) for c, o in zip(cases, outcomes, strict=True) if c.forbidden_keys]
    never = [
        c.id
        for c, o in with_forbidden
        if not {item["key"] for item in o.ranked} & set(c.forbidden_keys)
    ]
    return {
        "questions": len(with_forbidden),
        "forbidden_returned": len(with_forbidden) - len(never),
        "never_returned": never,
    }


async def run(
    session: AsyncSession,
    cases: list[RetrievalCase],
    modes: list[str],
    embedder,
    seed_memories: dict[str, SeedMemory],
    ids: dict[str, int],
    control: bool = False,
) -> dict[str, list[CaseOutcome]]:
    key_of = {memory_id: key for key, memory_id in ids.items()}
    outcomes: dict[str, list[CaseOutcome]] = {}
    for mode in modes:
        outcomes[mode] = []
        for case in cases:
            found = await search(
                session,
                case.question,
                now=EVAL_NOW,
                mode=mode,
                limit=DEPTH,
                embedder=embedder,
                include_external=case.include_external or control,
                include_upcoming=case.include_upcoming or control,
                include_inactive=control,
                include_ended=control,
            )
            ranked = [(key_of[r.memory_id], r.score) for r in found]
            outcomes[mode].append(evaluate_case(case, seed_memories, ranked))
    return outcomes


class CachedEmbedder:
    """Serves vectors computed up front, so every text is embedded once for all modes."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


class NoEmbeddings:
    """For a keyword-only run: the seed rows are stored without vectors."""

    async def embed(self, texts: list[str]) -> list[None]:  # type: ignore[override]
        return [None] * len(texts)


def prepare_database() -> str:
    """Create the eval database if needed and bring it to the current schema. Returns its URL."""
    url = make_url(settings.database_url)
    eval_url = url.set(database=EVAL_DATABASE).render_as_string(hide_password=False)
    admin_url = url.set(drivername="postgresql", database="postgres")
    try:
        with psycopg.connect(
            admin_url.render_as_string(hide_password=False),
            autocommit=True,
            connect_timeout=3,
        ) as admin:
            exists = admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (EVAL_DATABASE,)
            ).fetchone()
            if not exists:
                admin.execute(f"CREATE DATABASE {EVAL_DATABASE}")
    except psycopg.OperationalError as error:
        raise EvalError(f"Postgres is not reachable (make up): {error}") from error
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=os.environ | {"DATABASE_URL": eval_url},
        check=True,
        capture_output=True,
    )
    return eval_url


async def build_embedder(modes: list[str], cases: list[RetrievalCase], texts: list[str]):
    if all(mode == "keyword" for mode in modes):
        return NoEmbeddings()
    if not settings.openai_api_key:
        raise EvalError("OPENAI_API_KEY is not set; add it to .env, or run with --modes keyword")
    every = list(dict.fromkeys(texts + [case.question for case in cases]))
    vectors = await OpenAIEmbedder().embed(every)
    return CachedEmbedder(dict(zip(every, vectors, strict=True)))


def print_report(summaries: dict[str, dict]) -> None:
    k = RECALL_K
    print(f"\n{'mode':<15} {'recall@' + str(k):>9} {'MRR':>7} {'all@' + str(k):>7} {'leaks':>6}")
    for mode, s in summaries.items():
        print(
            f"{mode:<15} {s[f'recall@{k}']:>9.3f} {s['mrr']:>7.3f} {s[f'all@{k}']:>7.3f} "
            f"{s['leaks']:>6}"
        )
    print(f"\nrecall@{k} by category")
    categories = next(iter(summaries.values()))["by_category"]
    print(f"{'category':<15}{'n':>3} " + " ".join(f"{mode:>14}" for mode in summaries))
    for category, q in categories.items():
        scores = " ".join(
            f"{s['by_category'][category][f'recall@{k}']:>14.2f}" for s in summaries.values()
        )
        print(f"{category:<15}{q['questions']:>3} {scores}")
    print("\ntop score, median (min to max)")
    for mode, s in summaries.items():
        a, n = s["top_score"]["answerable"], s["top_score"]["no_answer"]
        print(
            f"{mode:<15} answerable {a['median']:.3f} ({a['min']:.3f} to {a['max']:.3f})"
            f"   no-answer {n['median']:.3f} ({n['min']:.3f} to {n['max']:.3f})"
        )


def report_control(
    split: str, cases: list[RetrievalCase], outcomes: dict[str, list[CaseOutcome]]
) -> int:
    summaries = {mode: summarize_control(cases, rows) for mode, rows in outcomes.items()}
    print("\nnegative control: every filter lifted, forbidden memories returned in the top 10")
    for mode, s in summaries.items():
        print(f"{mode:<15} {s['forbidden_returned']} of {s['questions']} questions")
        if s["never_returned"]:
            print(f"{'':<15} never returned: {', '.join(s['never_returned'])}")
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"memory-retrieval-control-{split}-{stamp}.json"
    path.write_text(json.dumps({"split": split, "summary": summaries}, indent=2))
    print(f"\nwrote {path.relative_to(ROOT.parent)}")
    return 0


async def main(split: str, modes: list[str], control: bool = False) -> int:
    cases = load_all()[split]
    seed_memories = load_seed()
    print(f"{split} split: {len(cases)} questions, modes: {', '.join(modes)}")
    eval_url = prepare_database()
    embedder = await build_embedder(modes, cases, [m.content for m in seed_memories])

    engine = create_async_engine(eval_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await reset(session)
            ids = await seed(session, embedder, seed_memories)
            outcomes = await run(
                session, cases, modes, embedder, {m.key: m for m in seed_memories}, ids, control
            )
    finally:
        await engine.dispose()
    if control:
        return report_control(split, cases, outcomes)
    summaries = {mode: summarize(o) for mode, o in outcomes.items()}
    print_report(summaries)

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"memory-retrieval-{split}-{stamp}.json"
    report = {
        "split": split,
        "created_at": stamp,
        "depth": DEPTH,
        "eval_now": EVAL_NOW.isoformat(),
        "recency_weight": settings.recency_weight,
        "recency_half_life_days": settings.recency_half_life_days,
        "summary": summaries,
        "rows": {mode: [asdict(o) for o in rows] for mode, rows in outcomes.items()},
    }
    path.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {path.relative_to(ROOT.parent)}")

    leaks = sum(s["leaks"] for s in summaries.values())
    if leaks:
        print(f"GATE FAILED: {leaks} leaks (see the leaks field of each row in the results file)")
        return 1
    print("gate: zero leaks")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Memory retrieval eval")
    parser.add_argument("--split", choices=SPLITS, default="dev")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument(
        "--control", action="store_true", help="lift every filter and report what comes back"
    )
    arguments = parser.parse_args()
    try:
        sys.exit(asyncio.run(main(arguments.split, arguments.modes, arguments.control)))
    except (EvalError, InvalidDataset) as error:
        sys.exit(f"error: {error}")
