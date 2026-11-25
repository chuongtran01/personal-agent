"""Tool eval: plays each case through the real loop with a real model and the read tools, and checks
the result in code.

Run from ``backend/`` (``make eval-tools``):

    python ../evals/tool_eval.py [--split dev|test] [--tools on|off|both] [--repeat N] [--case ID]

The dev split is the one to tune against, read three times (``--repeat 3``); the test split is held
out and read once. The runner uses the ``agent_eval`` database, emptied and re-seeded on every run,
and the clock is fixed at ``EVAL_NOW``. It needs ``OPENAI_API_KEY``: the seed memories, the fake
mail and calendar the tools return, every question and every answer go to the provider.

The tools are ``FakeTools`` (``tool_fixtures.py``): the three read tools of phase 6 over a made-up
mailbox and calendar, standing in for the MCP servers. ``--tools off`` plays the same cases with no
tool at all, the baseline that must say it cannot look. Both arms keep the memory writer on, as in
use, because one thing the cases check is that nothing the tools return reaches memory. Cases run
one at a time and every case starts from the same memory (the seeded rows are snapshotted once and
put back before each).

Exit status 1 means a gate failed: a memory in a prompt that may not be there, a memory changed by a
case, or a message read that the case forbids. The first runs, with the prompt as it was before
phase 6 changed it, are expected to show where those happen.
"""

import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from agent_eval import Turned, describe_changes, percentile, play
from memory_cases import EVAL_NOW, InvalidDataset
from memory_retrieval_eval import EvalError, prepare_database
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tool_cases import ToolCase, load_all
from tool_checks import check_case
from tool_fixtures import FakeTools

from app.agent import store
from app.agent.loop import AgentLoop
from app.agent.openai_client import OpenAIClient
from app.config import settings
from app.memory.embeddings import OpenAIEmbedder
from app.memory.seed import SeedMemory, Snapshot, load_seed, reset, restore, seed, snapshot
from app.memory.writer import MemoryWriter
from app.models import Memory

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
SPLITS = ("dev", "test")
ARMS = {"on": [True], "off": [False], "both": [True, False]}


@dataclass(frozen=True)
class CaseRun:
    id: str
    category: str
    run: int
    tools: bool
    passed: bool
    failures: tuple[str, ...]
    leaks: tuple[str, ...]
    calls_ok: bool | None
    calls: tuple[str, ...]  # "name(arguments)" in order, for reading a failure
    answer: str
    error: str | None
    input_tokens: int
    output_tokens: int
    steps: int
    total_seconds: float
    changes: tuple[str, ...]  # what the case changed in memory; none is expected


def describe_call(name: str, arguments: dict) -> str:
    return f"{name}({json.dumps(arguments, ensure_ascii=False)})"


async def run_case(
    session_factory,
    llm,
    embedder,
    writer: MemoryWriter | None,
    case: ToolCase,
    run: int,
    tools_on: bool,
    seed_memories: dict[str, SeedMemory],
    key_of: dict[int, str],
    saved: Snapshot,
) -> CaseRun:
    """Play the earlier turns and the judged message in a fresh chat, then check the last answer.

    Memory is put back to the snapshot before the case, and what the agent changed during it is
    recorded. Each turn waits for the memory write it queued, so a write is never in flight when
    the next case restores the table."""
    async with session_factory() as db:
        await restore(db, saved)
    async with session_factory() as db:
        session_id = await store.create_session(db)
    tools = FakeTools(failure=case.failure) if tools_on else None
    loop = AgentLoop(
        session_factory,
        llm,
        embedder,
        tools=tools,
        clock=lambda: EVAL_NOW,
        memory_writer=writer,
        retry_delay=0,
    )

    async def say(text_: str) -> Turned:
        played = await play(loop, session_id, text_, key_of)
        if writer is not None:
            await writer.drain()
        return played

    turned = Turned()
    for earlier in case.prior_turns:
        turned = await say(earlier)
        if turned.error:
            break
    if not turned.error:
        turned = await say(case.message)
    async with session_factory() as db:
        rows = (await db.execute(select(Memory.id, Memory.content, Memory.status))).all()
    changes = describe_changes(saved, [tuple(row) for row in rows], key_of)

    calls = tools.calls if tools else []
    verdict = check_case(
        case,
        seed_memories,
        calls,
        turned.current,
        turned.past,
        turned.answer,
        turned.error,
        changes,
    )
    done = turned.done
    return CaseRun(
        id=case.id,
        category=case.category,
        run=run,
        tools=tools_on,
        passed=verdict.passed,
        failures=verdict.failures,
        leaks=verdict.leaks,
        calls_ok=verdict.calls_ok,
        calls=tuple(describe_call(c.name, c.arguments) for c in calls),
        answer=turned.answer,
        error=turned.error,
        input_tokens=done.usage.input_tokens if done else 0,
        output_tokens=done.usage.output_tokens if done else 0,
        steps=done.steps if done else 0,
        total_seconds=done.total_seconds if done else 0.0,
        changes=changes,
    )


def _rate(runs: list[CaseRun]) -> dict:
    return {"runs": len(runs), "passed": sum(r.passed for r in runs)}


def _starts(run: CaseRun, *prefixes: str) -> bool:
    return any(f.startswith(prefixes) for f in run.failures)


def summarize(runs: list[CaseRun]) -> dict:
    """One arm: pass rate by category, tool-call accuracy, the gate counts, flaky cases, cost."""
    by_case: dict[str, list[CaseRun]] = {}
    for r in runs:
        by_case.setdefault(r.id, []).append(r)
    needs_calls = [r for r in runs if r.calls_ok is not None]
    answered = [r for r in runs if not r.error]
    return {
        "runs": len(runs),
        "passed": sum(r.passed for r in runs),
        "errors": sum(1 for r in runs if r.error),
        "leaks": sum(len(r.leaks) for r in runs),
        "memory_changed": sum(1 for r in runs if r.changes),
        "forbidden_reads": sum(1 for r in runs if _starts(r, "forbidden_read")),
        "too_many_calls": sum(1 for r in runs if _starts(r, "too_many_calls")),
        "by_category": {
            c: _rate([r for r in runs if r.category == c])
            for c in sorted({r.category for r in runs})
        },
        "right_calls": {
            "runs": len(needs_calls),
            "passed": sum(bool(r.calls_ok) for r in needs_calls),
        },
        "tool_calls_per_case": statistics.fmean(len(r.calls) for r in runs) if runs else 0.0,
        "flaky": sorted(i for i, rs in by_case.items() if 0 < sum(r.passed for r in rs) < len(rs)),
        "always_failing": sorted(i for i, rs in by_case.items() if not any(r.passed for r in rs)),
        "tokens_per_message": {
            "input": statistics.fmean(r.input_tokens for r in answered) if answered else 0.0,
            "output": statistics.fmean(r.output_tokens for r in answered) if answered else 0.0,
        },
        "seconds": {
            "p50": percentile([r.total_seconds for r in answered], 0.5),
            "p95": percentile([r.total_seconds for r in answered], 0.95),
        },
    }


def print_report(summaries: dict[str, dict]) -> None:
    arms = list(summaries)
    print(f"\n{'':<22}" + "".join(f"{'tools ' + a:>14}" for a in arms))

    def row(label, fn):
        print(f"{label:<22}" + "".join(f"{fn(summaries[a]):>14}" for a in arms))

    row("passed", lambda s: f"{s['passed']}/{s['runs']}")
    row("right tool and args", lambda s: f"{s['right_calls']['passed']}/{s['right_calls']['runs']}")
    row("memory in a prompt", lambda s: s["leaks"])
    row("memory changed", lambda s: s["memory_changed"])
    row("forbidden reads", lambda s: s["forbidden_reads"])
    row("too many calls", lambda s: s["too_many_calls"])
    row("turn errors", lambda s: s["errors"])
    row("calls per case", lambda s: f"{s['tool_calls_per_case']:.1f}")
    row(
        "tokens in/out",
        lambda s: f"{s['tokens_per_message']['input']:.0f}/{s['tokens_per_message']['output']:.0f}",
    )
    row("seconds p50", lambda s: f"{s['seconds']['p50']:.1f}")
    row("seconds p95", lambda s: f"{s['seconds']['p95']:.1f}")

    print("\npassed by category")
    for category in next(iter(summaries.values()))["by_category"]:
        cells = "".join(
            f"{summaries[a]['by_category'][category]['passed']}/"
            f"{summaries[a]['by_category'][category]['runs']}".rjust(14)
            for a in arms
        )
        print(f"{category:<22}{cells}")
    for arm, s in summaries.items():
        if s["flaky"]:
            print(f"\nflaky with tools {arm} (some runs pass, some fail): {', '.join(s['flaky'])}")


def print_failures(runs: list[CaseRun]) -> None:
    shown = set()
    for r in runs:
        if not r.passed and r.id not in shown:
            shown.add(r.id)
            print(f"  {r.id} [{r.category}] {', '.join(r.failures)}")
            print(f"      answer: {r.answer[:160]!r}")
            for call in r.calls:
                print(f"      call: {call[:150]}")
            for change in r.changes:
                print(f"      memory: {change}")


def gate_failures(summaries: dict[str, dict]) -> list[str]:
    """What the gate counts, with tools on: a leak, a changed memory, a forbidden read."""
    on = summaries.get("on")
    if on is None:
        return []
    problems = []
    for key, label in (
        ("leaks", "memories in a prompt that may not be there"),
        ("memory_changed", "runs that changed memory"),
        ("forbidden_reads", "forbidden messages read"),
    ):
        if on[key]:
            problems.append(f"{on[key]} {label}")
    return problems


async def main(split: str, arms: list[bool], repeat: int, only: list[str]) -> int:
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
        f"{split} split: {len(cases)} cases x {repeat} run(s), tools "
        f"{'/'.join('on' if a else 'off' for a in arms)}, model {settings.agent_model}"
    )
    engine = create_async_engine(prepare_database())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    writer = None
    try:
        async with session_factory() as db:
            await reset(db)
            await db.execute(text("TRUNCATE chat_messages, chat_sessions RESTART IDENTITY CASCADE"))
            await db.commit()
            embedder = OpenAIEmbedder()
            ids = await seed(db, embedder, seed_memories)
            saved = await snapshot(db)
        key_of = {memory_id: key for key, memory_id in ids.items()}
        llm = OpenAIClient()
        writer = MemoryWriter(
            session_factory,
            OpenAIClient(model=settings.extraction_model or settings.agent_model),
            embedder,
        )
        runs: dict[bool, list[CaseRun]] = {}
        for tools_on in arms:
            runs[tools_on] = [
                await run_case(
                    session_factory,
                    llm,
                    embedder,
                    writer,
                    case,
                    run,
                    tools_on,
                    by_key,
                    key_of,
                    saved,
                )
                for run in range(1, repeat + 1)
                for case in cases
            ]
    finally:
        if writer is not None:
            await writer.close()
        await engine.dispose()

    summaries = {("on" if a else "off"): summarize(r) for a, r in runs.items()}
    print_report(summaries)
    for tools_on, arm_runs in runs.items():
        print(f"\nfailing cases, tools {'on' if tools_on else 'off'} (first failing run of each):")
        print_failures(arm_runs)

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"tools-{split}-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "split": split,
                "created_at": stamp,
                "model": settings.agent_model,
                "repeat": repeat,
                "eval_now": EVAL_NOW.isoformat(),
                "summary": summaries,
                "runs": [asdict(r) for arm_runs in runs.values() for r in arm_runs],
            },
            indent=2,
        )
    )
    print(f"\nwrote {path.relative_to(ROOT.parent)}")

    if all(s["errors"] == s["runs"] for s in summaries.values()):
        print("every turn ended in an error: the model could not be reached (check the key)")
        return 1
    problems = gate_failures(summaries)
    if problems:
        print("GATE FAILED: " + "; ".join(problems))
        return 1
    print("gate: no leak, no memory changed, no forbidden read")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool eval")
    parser.add_argument("--split", choices=SPLITS, default="dev")
    parser.add_argument("--tools", choices=list(ARMS), default="both")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case", action="append", default=[], help="run only this case id")
    arguments = parser.parse_args()
    if arguments.repeat < 1:
        sys.exit("error: --repeat is at least 1")
    try:
        sys.exit(
            asyncio.run(
                main(arguments.split, ARMS[arguments.tools], arguments.repeat, arguments.case)
            )
        )
    except (EvalError, InvalidDataset) as error:
        sys.exit(f"error: {error}")
