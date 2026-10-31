"""Extraction eval: gives each case to the memory-writing step and checks its changes in code.

Run from ``backend/`` (``make eval-extraction``):

    python ../evals/extraction_eval.py [--split dev|test] [--writer none] [--repeat N]

There is no agent, no database and no search here: a writer gets one case (the memories that
already exist, the earlier messages, the owner's turn and the assistant's reply) and answers with
the changes it would make. ``extraction_checks`` judges them. ``--writer none`` is the baseline
that stores nothing, so what it passes is exactly the cases that ask for no change; the real
writer is added when the extractor exists.

The dev split is the one to tune against, read three times (``--repeat 3``); the test split is
held out and read once. The gate is zero tolerance: a forbidden word or a card-like number in a
new memory, or a delete or replacement of a memory the owner did not mean, in any run. A run with
one exits with status 1.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from extraction_cases import SPLITS, ExtractionCase, InvalidDataset, load_cases
from extraction_checks import Change, check_case
from memory_cases import EVAL_NOW

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


@dataclass(frozen=True)
class Outcome:
    changes: list[Change] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class Writer(Protocol):
    async def run(self, case: ExtractionCase, now: datetime) -> Outcome: ...


class NoWrites:
    """The baseline: never changes anything."""

    async def run(self, case: ExtractionCase, now: datetime) -> Outcome:
        return Outcome()


WRITERS = {"none": NoWrites}


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


async def run_case(writer: Writer, case: ExtractionCase, run: int) -> CaseRun:
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
    )


def summarize(runs: list[CaseRun]) -> dict:
    by_category: dict[str, dict[str, int]] = {}
    for item in runs:
        row = by_category.setdefault(item.category, {"runs": 0, "passed": 0})
        row["runs"] += 1
        row["passed"] += item.passed
    count = len(runs)
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
    }


def print_report(summary: dict) -> None:
    print(f"{'category':18}{'passed':>10}")
    for category, row in sorted(summary["by_category"].items()):
        print(f"{category:18}{row['passed']:>5}/{row['runs']:<4}")
    print(f"{'all':18}{summary['passed']:>5}/{summary['runs']:<4}")
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
    if not seen:
        print("  none")


async def main(
    split: str,
    writer_name: str,
    repeat: int,
    only: list[str],
    results_dir: Path = RESULTS,
) -> int:
    cases = load_cases(split)
    if only:
        unknown = sorted(set(only) - {case.id for case in cases})
        if unknown:
            raise InvalidDataset(f"unknown case ids in {split}: {', '.join(unknown)}")
        cases = [case for case in cases if case.id in only]
    writer = WRITERS[writer_name]()

    runs = [await run_case(writer, case, run) for run in range(1, repeat + 1) for case in cases]
    summary = summarize(runs)
    print(f"{split}: {len(cases)} cases x {repeat}, writer {writer_name}\n")
    print_report(summary)
    print("\nfailing cases (first failing run of each):")
    print_failures(runs)

    results_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = results_dir / f"extraction-{split}-{writer_name}-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "split": split,
                "writer": writer_name,
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
    parser = argparse.ArgumentParser(description="Extraction eval")
    parser.add_argument("--split", choices=SPLITS, default="dev")
    parser.add_argument("--writer", choices=list(WRITERS), default="none")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case", action="append", default=[], help="run only this case id")
    arguments = parser.parse_args()
    if arguments.repeat < 1:
        sys.exit("error: --repeat is at least 1")
    try:
        sys.exit(
            asyncio.run(main(arguments.split, arguments.writer, arguments.repeat, arguments.case))
        )
    except InvalidDataset as error:
        sys.exit(f"error: {error}")
