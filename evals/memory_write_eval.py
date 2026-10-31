"""Memory write eval: gives each case to the memory-writing step and checks its changes in code.

Run from ``backend/`` (``make eval-memory-write``):

    python ../evals/memory_write_eval.py [--split dev|test] [--writer none|extract|pipeline] [--repeat N]

There is no agent, no database and no search here: a writer gets one case (the memories that
already exist, the earlier messages, the owner's turn and the assistant's reply) and answers with
the changes it would make. ``memory_write_checks`` judges them.

``--writer none`` is the baseline that stores nothing, so what it passes is exactly the cases that
ask for no change. ``--writer extract`` runs the extractor alone (``app/memory/extract.py``) on a
real model, so it needs ``OPENAI_API_KEY`` and sends the cases' text to the provider: each proposed
``add`` becomes an ``Add``. It has no reconciliation yet, so it can neither see the existing
memories nor delete one: the duplicate, supersede and delete cases fail by design.

``--writer pipeline`` adds reconciliation (``app/memory/reconcile.py``) against the memories
written in the case, found by cosine similarity on real embeddings, so it also needs the key and
sends the stored text to the embeddings API. Every decision is read back as the change it makes.

The dev split is the one to tune against, read three times (``--repeat 3``); the test split is
held out and read once. The gate is zero tolerance: a forbidden word or a card-like number in a
new memory, or a delete or replacement of a memory the owner did not mean, in any run. A run with
one exits with status 1.
"""

import argparse
import asyncio
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from memory_cases import EVAL_NOW
from memory_write_cases import EVAL_TZ, SPLITS, InvalidDataset, MemoryWriteCase, load_cases
from memory_write_checks import Add, Change, Delete, Supersede, check_case

from app.agent.llm import StructuredLLM
from app.agent.openai_client import OpenAIClient
from app.config import settings
from app.memory.embeddings import Embedder, OpenAIEmbedder
from app.memory.extract import ContextMessage, extract
from app.memory.reconcile import Candidate, reconcile

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


@dataclass(frozen=True)
class Outcome:
    changes: list[Change] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    notes: list[str] = field(default_factory=list)  # how the writer got there, for a failure


class Writer(Protocol):
    async def run(self, case: MemoryWriteCase, now: datetime) -> Outcome: ...


class NoWrites:
    """The baseline: never changes anything."""

    async def run(self, case: MemoryWriteCase, now: datetime) -> Outcome:
        return Outcome()


class ExtractOnly:
    """The extractor on its own: every proposed add is stored as it is, nothing else changes."""

    def __init__(self, llm: StructuredLLM | None = None):
        self._llm = llm or OpenAIClient(model=settings.extraction_model or settings.agent_model)

    async def run(self, case: MemoryWriteCase, now: datetime) -> Outcome:
        found = await extract(
            self._llm,
            owner=case.owner,
            now=now,
            timezone=EVAL_TZ.key,
            context=[ContextMessage(m.role, m.text) for m in case.context],
            reply=case.reply,
        )
        changes: list[Change] = [
            Add(p.kind or "fact", p.content, None, p.valid_until)
            for p in found.proposals
            if p.op == "add"
        ]
        return Outcome(changes, found.usage.input_tokens, found.usage.output_tokens)


class InlineNeighbours:
    """``Neighbours`` over the memories written inside a case, by cosine similarity in Python.

    Only active memories are candidates, as in the database. A candidate's id is its position in
    the case's list, and ``keys`` maps it back to the case's own key."""

    def __init__(self, case: MemoryWriteCase, embedder: Embedder, vectors: dict[str, list[float]]):
        self._embedder = embedder
        self._vectors = vectors
        self._active = [(n, m) for n, m in enumerate(case.existing) if m.status == "active"]
        self.keys = {n: m.key for n, m in self._active}

    async def _vector(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._vectors]
        if missing:
            self._vectors.update(zip(missing, await self._embedder.embed(missing), strict=True))
        return [self._vectors[t] for t in texts]

    async def nearest(self, text: str, limit: int) -> list[Candidate]:
        if not self._active:
            return []
        query, *stored = await self._vector([text, *(m.content for _, m in self._active)])
        found = [
            Candidate(
                n, m.kind, m.content, EVAL_NOW - timedelta(days=30), "owner", _cosine(query, v)
            )
            for (n, m), v in zip(self._active, stored, strict=True)
        ]
        return sorted(found, key=lambda c: (-c.similarity, c.id))[:limit]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


class _Recording:
    """Wraps a lookup and keeps what it found, to read the similarities when a case fails."""

    def __init__(self, inner: InlineNeighbours):
        self._inner = inner
        self.keys = inner.keys
        self.notes: list[str] = []

    async def nearest(self, text: str, limit: int) -> list[Candidate]:
        found = await self._inner.nearest(text, limit)
        top = ", ".join(f"{self.keys[c.id]} {c.similarity:.2f}" for c in found[:3])
        self.notes.append(f"nearest to {text!r}: {top or 'nothing stored'}")
        return found


class Pipeline:
    """The write path as far as it is built: extract, then reconcile against the case's memories.

    Every decision becomes the change it would make: a new memory, a replacement, a delete, or
    nothing when a stored memory already says it or nothing matches a request to forget."""

    def __init__(self, llm: StructuredLLM | None = None, embedder: Embedder | None = None):
        self._llm = llm or OpenAIClient(model=settings.extraction_model or settings.agent_model)
        self._embedder = embedder or OpenAIEmbedder()
        self._vectors: dict[str, list[float]] = {}

    async def run(self, case: MemoryWriteCase, now: datetime) -> Outcome:
        found = await extract(
            self._llm,
            owner=case.owner,
            now=now,
            timezone=EVAL_TZ.key,
            context=[ContextMessage(m.role, m.text) for m in case.context],
            reply=case.reply,
        )
        lookup = _Recording(InlineNeighbours(case, self._embedder, self._vectors))
        done = await reconcile(self._llm, lookup, found.proposals, owner=case.owner)

        changes: list[Change] = []
        notes = lookup.notes
        for decision in done.decisions:
            keys = [lookup.keys[i] for i in decision.memory_ids]
            proposal = decision.proposal
            kind = proposal.kind or "fact"
            notes.append(f"{decision.action} {keys} <- {proposal.op} {proposal.content!r}")
            if decision.action == "add":
                changes.append(Add(kind, proposal.content, None, proposal.valid_until))
            elif decision.action == "supersede":
                changes.append(
                    Supersede(
                        keys[0], kind, proposal.content, None, proposal.valid_until, tuple(keys[1:])
                    )
                )
            elif decision.action == "delete":
                changes += [Delete(key) for key in keys]
        if done.dropped:
            notes.append(f"dropped {done.dropped} proposal(s) the model's answer could not back")
        usage = (
            found.usage.input_tokens + done.usage.input_tokens,
            found.usage.output_tokens + done.usage.output_tokens,
        )
        return Outcome(changes, *usage, notes)


WRITERS = {"none": NoWrites, "extract": ExtractOnly, "pipeline": Pipeline}


@dataclass(frozen=True)
class CaseRun:
    id: str
    category: str
    run: int
    passed: bool
    failures: tuple[str, ...]
    stored: tuple[str, ...]
    wrong_changes: tuple[str, ...]
    changes: int
    error: str | None
    input_tokens: int
    output_tokens: int
    proposed: tuple[str, ...] = ()  # what the writer changed, to read a failure without a rerun
    needs_reconciliation: bool = False  # the case has existing memories (see the cases module)
    notes: tuple[str, ...] = ()  # how the writer got to its changes


def _describe(change: Change) -> str:
    if isinstance(change, Delete):
        return f"delete {change.target}"
    until = f" (until {change.valid_until.isoformat()})" if change.valid_until else ""
    target = f"replace {change.target}: " if isinstance(change, Supersede) else "add "
    return f"{target}[{change.kind}] {change.content}{until}"


async def run_case(writer: Writer, case: MemoryWriteCase, run: int) -> CaseRun:
    try:
        outcome = await writer.run(case, EVAL_NOW)
    except Exception as error:  # noqa: BLE001 - a writer that fails fails its case, not the run
        return CaseRun(
            id=case.id,
            category=case.category,
            run=run,
            passed=False,
            failures=(f"error:{type(error).__name__}",),
            stored=(),
            wrong_changes=(),
            changes=0,
            error=f"{type(error).__name__}: {error}",
            input_tokens=0,
            output_tokens=0,
            needs_reconciliation=case.needs_reconciliation,
        )
    verdict = check_case(case, outcome.changes)
    return CaseRun(
        id=case.id,
        category=case.category,
        run=run,
        passed=verdict.passed,
        failures=verdict.failures,
        stored=verdict.stored,
        wrong_changes=verdict.wrong_changes,
        changes=len(outcome.changes),
        error=None,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        proposed=tuple(_describe(change) for change in outcome.changes),
        needs_reconciliation=case.needs_reconciliation,
        notes=tuple(outcome.notes),
    )


def summarize(runs: list[CaseRun]) -> dict:
    by_category: dict[str, dict[str, int]] = {}
    for item in runs:
        row = by_category.setdefault(item.category, {"runs": 0, "passed": 0})
        row["runs"] += 1
        row["passed"] += item.passed
    count = len(runs)
    by_stage = {
        name: {
            "runs": sum(item.needs_reconciliation is needs for item in runs),
            "passed": sum(item.passed for item in runs if item.needs_reconciliation is needs),
        }
        for name, needs in (("alone", False), ("needs_reconciliation", True))
    }
    return {
        "runs": count,
        "passed": sum(item.passed for item in runs),
        "errors": sum(item.error is not None for item in runs),
        "stored": sum(len(item.stored) for item in runs),
        "wrong_changes": sum(len(item.wrong_changes) for item in runs),
        "changes_per_turn": round(sum(item.changes for item in runs) / count, 2) if count else 0,
        "input_tokens": sum(item.input_tokens for item in runs),
        "output_tokens": sum(item.output_tokens for item in runs),
        "by_category": by_category,
        "by_stage": by_stage,
    }


def print_report(summary: dict) -> None:
    print(f"{'category':18}{'passed':>10}")
    for category, row in sorted(summary["by_category"].items()):
        print(f"{category:18}{row['passed']:>5}/{row['runs']:<4}")
    print(f"{'all':18}{summary['passed']:>5}/{summary['runs']:<4}")
    alone, later = summary["by_stage"]["alone"], summary["by_stage"]["needs_reconciliation"]
    print(f"  no existing memories (the extractor alone)  {alone['passed']}/{alone['runs']}")
    print(f"  existing memories (need reconciliation)     {later['passed']}/{later['runs']}")
    print(
        f"changes per turn {summary['changes_per_turn']}, errors {summary['errors']}, "
        f"tokens in/out {summary['input_tokens']}/{summary['output_tokens']}"
    )
    print(f"stored: {summary['stored']}, wrong changes: {summary['wrong_changes']}")


def print_failures(runs: list[CaseRun]) -> None:
    seen: set[str] = set()
    for item in runs:
        if not item.passed and item.id not in seen:
            seen.add(item.id)
            print(f"  {item.id} ({item.category}): {', '.join(item.failures)}")
            for line in item.proposed:
                print(f"      wrote: {line}")
            for line in item.notes:
                print(f"      note: {line}")
    if not seen:
        print("  none")


async def main(
    split: str,
    writer_name: str,
    repeat: int,
    only: list[str],
    results_dir: Path = RESULTS,
    concurrency: int = 4,
) -> int:
    cases = load_cases(split)
    if only:
        unknown = sorted(set(only) - {case.id for case in cases})
        if unknown:
            raise InvalidDataset(f"unknown case ids in {split}: {', '.join(unknown)}")
        cases = [case for case in cases if case.id in only]
    writer = WRITERS[writer_name]()

    gate = asyncio.Semaphore(concurrency)

    async def one(case: MemoryWriteCase, run: int) -> CaseRun:
        async with gate:
            return await run_case(writer, case, run)

    runs = list(
        await asyncio.gather(*(one(case, run) for run in range(1, repeat + 1) for case in cases))
    )
    summary = summarize(runs)
    print(f"{split}: {len(cases)} cases x {repeat}, writer {writer_name}\n")
    print_report(summary)
    print("\nfailing cases (first failing run of each):")
    print_failures(runs)

    results_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = results_dir / f"memory-write-{split}-{writer_name}-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "split": split,
                "writer": writer_name,
                "model": (settings.extraction_model or settings.agent_model)
                if writer_name == "extract"
                else None,
                "created_at": stamp,
                "repeat": repeat,
                "eval_now": EVAL_NOW.isoformat(),
                "summary": summary,
                "runs": [asdict(item) for item in runs],
            },
            indent=2,
        )
    )
    try:
        shown = path.relative_to(ROOT.parent)
    except ValueError:
        shown = path
    print(f"\nwrote {shown}")

    if summary["stored"] or summary["wrong_changes"]:
        print(
            "GATE FAILED: see the stored and wrong_changes fields of each run in the results file"
        )
        return 1
    print("gate: nothing forbidden stored, no wrong change")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Memory write eval")
    parser.add_argument("--split", choices=SPLITS, default="dev")
    parser.add_argument("--writer", choices=list(WRITERS), default="none")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case", action="append", default=[], help="run only this case id")
    parser.add_argument("--concurrency", type=int, default=4, help="model calls at once")
    arguments = parser.parse_args()
    if arguments.repeat < 1 or arguments.concurrency < 1:
        sys.exit("error: --repeat and --concurrency are at least 1")
    try:
        sys.exit(
            asyncio.run(
                main(
                    arguments.split,
                    arguments.writer,
                    arguments.repeat,
                    arguments.case,
                    concurrency=arguments.concurrency,
                )
            )
        )
    except (InvalidDataset, RuntimeError) as error:  # RuntimeError: no OPENAI_API_KEY
        sys.exit(f"error: {error}")
