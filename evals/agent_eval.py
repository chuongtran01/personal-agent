"""Agent eval: plays each case through the real loop with a real model and checks the result in code.

Run from ``backend/`` (``make eval-agent``):

    python ../evals/agent_eval.py [--split dev|test] [--memory on|off|both] [--repeat N]

The dev split is the one to tune against, read three times (``--repeat 3``); the test split is
held out and read once. The runner uses its own database, ``agent_eval``, emptied and re-seeded on
every run, and the clock is fixed at ``EVAL_NOW``, so a "this week" in a case means the same on
every day. It needs ``OPENAI_API_KEY``: the seed memories, every question and every answer go to
the provider. A run costs a few cents.

``--memory off`` plays the same cases with recall switched off. The cases that need a seeded fact
should then fail, and the gap to the ``on`` arm is the measured effect of memory; ``both`` runs the
two and prints them side by side.

The gate is zero leaks: no memory that may not be used reaches a prompt, in any run. A run with a
leak exits with status 1.
"""

import argparse
import asyncio
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from agent_cases import AgentCase, load_all
from agent_checks import check_case
from memory_cases import EVAL_NOW, InvalidDataset
from memory_retrieval_eval import MODES, EvalError, prepare_database
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent import store
from app.agent.llm import TextDelta
from app.agent.loop import AgentDone, AgentLoop, MemoryRecalled, TurnError, TurnRefused
from app.agent.openai_client import OpenAIClient
from app.config import settings
from app.memory.embeddings import OpenAIEmbedder
from app.memory.seed import SeedMemory, load_seed, reset, seed

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
SPLITS = ("dev", "test")
ARMS = {"on": [True], "off": [False], "both": [True, False]}


@dataclass(frozen=True)
class CaseRun:
    id: str
    category: str
    run: int
    memory: bool
    passed: bool
    failures: tuple[str, ...]
    leaks: tuple[str, ...]
    recall_ok: bool | None
    recalled: tuple[str, ...]  # seed keys in the current block
    recalled_past: tuple[str, ...]  # seed keys in the history block
    answer: str
    error: str | None
    input_tokens: int
    output_tokens: int
    steps: int
    recall_seconds: float
    total_seconds: float


@dataclass
class Turned:
    current: tuple[str, ...] = ()
    past: tuple[str, ...] = ()
    answer: str = ""
    error: str | None = None
    done: AgentDone | None = None
    _chunks: list[str] = field(default_factory=list)


async def play(loop: AgentLoop, session_id, text: str, memory: bool, key_of: dict[int, str]):
    """One owner message through the loop, with everything it reported."""
    turned = Turned()
    try:
        turn = await loop.start(session_id, text, memory=memory)
    except TurnRefused as refusal:
        turned.error = refusal.code
        return turned
    async for event in turn.events():
        if isinstance(event, MemoryRecalled):
            turned.current = tuple(
                key_of.get(r.memory_id, f"id:{r.memory_id}") for r in event.current
            )
            turned.past = tuple(key_of.get(r.memory_id, f"id:{r.memory_id}") for r in event.past)
        elif isinstance(event, TextDelta):
            turned._chunks.append(event.text)
        elif isinstance(event, AgentDone):
            turned.done = event
        elif isinstance(event, TurnError):
            turned.error = event.code
    turned.answer = "".join(turned._chunks)
    return turned


async def run_case(
    loop: AgentLoop,
    session_factory,
    case: AgentCase,
    run: int,
    memory: bool,
    seed_memories: dict[str, SeedMemory],
    key_of: dict[int, str],
) -> CaseRun:
    """Play the earlier turns and the judged message in a fresh chat, then check the last answer."""
    async with session_factory() as db:
        session_id = await store.create_session(db)
    turned = Turned()
    for earlier in case.prior_turns:
        turned = await play(loop, session_id, earlier, memory, key_of)
        if turned.error:
            break
    if not turned.error:
        turned = await play(loop, session_id, case.message, memory, key_of)

    verdict = check_case(
        case, seed_memories, turned.current, turned.past, turned.answer, turned.error
    )
    done = turned.done
    return CaseRun(
        id=case.id,
        category=case.category,
        run=run,
        memory=memory,
        passed=verdict.passed,
        failures=verdict.failures,
        leaks=verdict.leaks,
        recall_ok=verdict.recall_ok,
        recalled=turned.current,
        recalled_past=turned.past,
        answer=turned.answer,
        error=turned.error,
        input_tokens=done.usage.input_tokens if done else 0,
        output_tokens=done.usage.output_tokens if done else 0,
        steps=done.steps if done else 0,
        recall_seconds=done.recall_seconds if done else 0.0,
        total_seconds=done.total_seconds if done else 0.0,
    )


def percentile(values: list[float], fraction: float) -> float:
    """Nearest rank, so a small run still reports a number that happened."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _rate(runs: list[CaseRun]) -> dict:
    return {"runs": len(runs), "passed": sum(r.passed for r in runs)}


def summarize(runs: list[CaseRun], price_in: float = 0.0, price_out: float = 0.0) -> dict:
    """One arm: pass rate by category, leaks, recall accuracy, flaky cases, tokens, latency."""
    by_case: dict[str, list[CaseRun]] = {}
    for r in runs:
        by_case.setdefault(r.id, []).append(r)
    needs_memory = [r for r in runs if r.recall_ok is not None]
    answered = [r for r in runs if not r.error]
    input_tokens = statistics.fmean(r.input_tokens for r in answered) if answered else 0.0
    output_tokens = statistics.fmean(r.output_tokens for r in answered) if answered else 0.0
    summary = {
        "runs": len(runs),
        "passed": sum(r.passed for r in runs),
        "errors": sum(1 for r in runs if r.error),
        "leaks": sum(len(r.leaks) for r in runs),
        "by_category": {
            c: _rate([r for r in runs if r.category == c])
            for c in sorted({r.category for r in runs})
        },
        "recall": {
            "runs": len(needs_memory),
            "all_required_recalled": sum(bool(r.recall_ok) for r in needs_memory),
        },
        "flaky": sorted(i for i, rs in by_case.items() if 0 < sum(r.passed for r in rs) < len(rs)),
        "always_failing": sorted(i for i, rs in by_case.items() if not any(r.passed for r in rs)),
        "tokens_per_message": {"input": input_tokens, "output": output_tokens},
        "seconds": {
            "total_p50": percentile([r.total_seconds for r in answered], 0.5),
            "total_p95": percentile([r.total_seconds for r in answered], 0.95),
            "recall_p50": percentile([r.recall_seconds for r in answered], 0.5),
            "recall_p95": percentile([r.recall_seconds for r in answered], 0.95),
        },
    }
    if price_in or price_out:
        summary["dollars_per_message"] = (input_tokens * price_in + output_tokens * price_out) / 1e6
    return summary


def memory_effect(on: list[CaseRun], off: list[CaseRun]) -> dict:
    """Pass rate of the cases that require a seeded fact, with recall on and off."""
    needs = {r.id for r in on if r.recall_ok is not None}

    def rate(runs: list[CaseRun]) -> dict:
        return _rate([r for r in runs if r.id in needs])

    return {"cases": len(needs), "on": rate(on), "off": rate(off)}


def print_report(summaries: dict[str, dict], effect: dict | None) -> None:
    arms = list(summaries)
    print(f"\n{'':<18}" + "".join(f"{'memory ' + a:>16}" for a in arms))

    def row(label, fn):
        print(f"{label:<18}" + "".join(f"{fn(summaries[a]):>16}" for a in arms))

    row("passed", lambda s: f"{s['passed']}/{s['runs']}")
    row("leaks", lambda s: s["leaks"])
    row("turn errors", lambda s: s["errors"])
    row(
        "required recalled",
        lambda s: f"{s['recall']['all_required_recalled']}/{s['recall']['runs']}",
    )
    row(
        "tokens in/out",
        lambda s: f"{s['tokens_per_message']['input']:.0f}/{s['tokens_per_message']['output']:.0f}",
    )
    row("seconds p50", lambda s: f"{s['seconds']['total_p50']:.1f}")
    row("seconds p95", lambda s: f"{s['seconds']['total_p95']:.1f}")
    row("recall p50", lambda s: f"{s['seconds']['recall_p50']:.2f}")
    if all("dollars_per_message" in s for s in summaries.values()):
        row("$ per message", lambda s: f"{s['dollars_per_message']:.5f}")

    print("\npassed by category")
    for category in next(iter(summaries.values()))["by_category"]:
        cells = ""
        for a in arms:
            c = summaries[a]["by_category"][category]
            cells += f"{c['passed']}/{c['runs']}".rjust(16)
        print(f"{category:<18}{cells}")
    if effect:
        print(
            f"\nthe {effect['cases']} cases that need a seeded fact: "
            f"memory on {effect['on']['passed']}/{effect['on']['runs']}, "
            f"memory off {effect['off']['passed']}/{effect['off']['runs']}"
        )
    for arm, s in summaries.items():
        if s["flaky"]:
            print(f"\nflaky with memory {arm} (some runs pass, some fail): {', '.join(s['flaky'])}")


def print_failures(runs: list[CaseRun]) -> None:
    shown = set()
    for r in runs:
        if not r.passed and r.id not in shown:
            shown.add(r.id)
            print(f"  {r.id} [{r.category}] {', '.join(r.failures)}")
            print(f"      answer: {r.answer[:160]!r}")


async def main(
    split: str,
    arms: list[bool],
    repeat: int,
    recall_mode: str | None,
    concurrency: int,
    only: list[str],
    price_in: float,
    price_out: float,
) -> int:
    cases = load_all()[split]
    if only:
        unknown = set(only) - {c.id for c in cases}
        if unknown:
            raise EvalError(f"not in the {split} split: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in only]
    if not settings.openai_api_key:
        raise EvalError("OPENAI_API_KEY is not set; add it to .env")
    seed_memories = load_seed()
    by_key = {m.key: m for m in seed_memories}
    print(
        f"{split} split: {len(cases)} cases x {repeat} run(s), memory "
        f"{'/'.join('on' if a else 'off' for a in arms)}, model {settings.agent_model}"
    )
    eval_url = prepare_database()
    engine = create_async_engine(eval_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            await reset(db)
            await db.execute(text("TRUNCATE chat_messages, chat_sessions RESTART IDENTITY CASCADE"))
            await db.commit()
            embedder = OpenAIEmbedder()
            ids = await seed(db, embedder, seed_memories)
        key_of = {memory_id: key for key, memory_id in ids.items()}
        loop = AgentLoop(
            session_factory,
            OpenAIClient(),
            embedder,
            clock=lambda: EVAL_NOW,
            recall_mode=recall_mode,
        )
        gate = asyncio.Semaphore(concurrency)

        async def one(case, run, memory):
            async with gate:
                return await run_case(loop, session_factory, case, run, memory, by_key, key_of)

        runs: dict[bool, list[CaseRun]] = {}
        for memory in arms:
            runs[memory] = list(
                await asyncio.gather(
                    *(one(case, run, memory) for run in range(1, repeat + 1) for case in cases)
                )
            )
    finally:
        await engine.dispose()

    summaries = {("on" if m else "off"): summarize(r, price_in, price_out) for m, r in runs.items()}
    effect = memory_effect(runs[True], runs[False]) if len(arms) == 2 else None
    print_report(summaries, effect)
    for memory, arm_runs in runs.items():
        print(f"\nfailing cases, memory {'on' if memory else 'off'} (first failing run of each):")
        print_failures(arm_runs)

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"agent-{split}-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "split": split,
                "created_at": stamp,
                "model": settings.agent_model,
                "recall_mode": recall_mode or settings.memory_search_mode,
                "recall_limit": settings.recall_limit,
                "repeat": repeat,
                "eval_now": EVAL_NOW.isoformat(),
                "summary": summaries,
                "memory_effect": effect,
                "runs": [asdict(r) for arm_runs in runs.values() for r in arm_runs],
            },
            indent=2,
        )
    )
    print(f"\nwrote {path.relative_to(ROOT.parent)}")

    if all(s["errors"] == s["runs"] for s in summaries.values()):
        print("every turn ended in an error: the model could not be reached (check the key)")
        return 1
    leaks = sum(s["leaks"] for s in summaries.values())
    if leaks:
        print(f"GATE FAILED: {leaks} leaks (see the leaks field of each run in the results file)")
        return 1
    print("gate: zero leaks")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent eval")
    parser.add_argument("--split", choices=SPLITS, default="dev")
    parser.add_argument("--memory", choices=list(ARMS), default="on")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--recall-mode", choices=MODES, default=None)
    parser.add_argument(
        "--concurrency", type=int, default=1, help="more is faster, and skews the seconds"
    )
    parser.add_argument("--case", action="append", default=[], help="run only this case id")
    parser.add_argument(
        "--price-in", type=float, default=0.0, help="dollars per million input tokens"
    )
    parser.add_argument(
        "--price-out", type=float, default=0.0, help="dollars per million output tokens"
    )
    arguments = parser.parse_args()
    if arguments.repeat < 1 or arguments.concurrency < 1:
        sys.exit("error: --repeat and --concurrency are at least 1")
    try:
        sys.exit(
            asyncio.run(
                main(
                    arguments.split,
                    ARMS[arguments.memory],
                    arguments.repeat,
                    arguments.recall_mode,
                    arguments.concurrency,
                    arguments.case,
                    arguments.price_in,
                    arguments.price_out,
                )
            )
        )
    except (EvalError, InvalidDataset) as error:
        sys.exit(f"error: {error}")
