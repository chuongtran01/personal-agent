"""Memory retrieval eval cases: the schema and the loader.

Each row of ``datasets/memory_retrieval.dev.jsonl`` and ``.test.jsonl`` is a question asked of the
seeded memories (``data/seed_memories.yaml``): which memories should come back, and which must
not. Memories are named by their seed key.

The loader checks every row against the seed file, so a mistake in the data is caught here and
not as a strange score later: a relevant memory must be one the search is allowed to return
under the row's flags, and a forbidden one must exist.
"""

import json
from pathlib import Path
from typing import Literal

import yaml
from memory_cases import EVAL_NOW, InvalidDataset
from pydantic import BaseModel, ConfigDict, ValidationError

from app.memory.seed import SEED_FILE, InvalidSeed, SeedMemory, load_seed

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
SEED = SEED_FILE
TRAPS = ROOT.parent / "data" / "memory_traps.yaml"

SPLITS = ("dev", "test")

Category = Literal[
    "direct",
    "paraphrase",
    "exact_term",
    "changed_fact",
    "forgotten",
    "stale_event",
    "multi",
    "near_duplicate",
    "unrelated",
    "external",
    "upcoming",
    "messy",
]


class RetrievalCase(BaseModel):
    """Every field is required, with an empty value where it does not apply, so all rows look alike."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    question: str
    relevant_keys: list[str]
    forbidden_keys: list[str]
    include_external: bool
    include_upcoming: bool
    expect_no_answer: bool
    trap_id: str | None
    note: str | None


def load_seed_index(path: Path = SEED) -> dict[str, SeedMemory]:
    """The seed memories by key, from the same loader that fills the database."""
    try:
        return {memory.key: memory for memory in load_seed(path)}
    except InvalidSeed as error:
        raise InvalidDataset(str(error)) from error


def why_not_returnable(memory: SeedMemory, case: RetrievalCase) -> str | None:
    if memory.status != "active":
        return f"is {memory.status}"
    if memory.source_kind == "external" and not case.include_external:
        return "is external and the row does not include external memories"
    if memory.valid_until and memory.valid_until <= EVAL_NOW:
        return "has already ended"
    if memory.valid_from and memory.valid_from > EVAL_NOW and not case.include_upcoming:
        return "has not started and the row does not include upcoming facts"
    return None


def load_cases(
    split: str,
    directory: Path = DATASETS,
    seed_path: Path = SEED,
    traps_path: Path = TRAPS,
) -> list[RetrievalCase]:
    """The cases of a split. An empty file is a valid, empty split."""
    if split not in SPLITS:
        raise InvalidDataset(f"unknown split {split!r}; expected one of {SPLITS}")
    path = directory / f"memory_retrieval.{split}.jsonl"
    cases: list[RetrievalCase] = []
    try:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.strip():
                try:
                    cases.append(RetrievalCase.model_validate(json.loads(line)))
                except (json.JSONDecodeError, ValidationError) as error:
                    raise InvalidDataset(f"{path.name} line {number}: {error}") from error
    except OSError as error:
        raise InvalidDataset(f"{path.name}: {error}") from error

    seed = load_seed_index(seed_path)
    try:
        trap_ids = {row["id"] for row in yaml.safe_load(traps_path.read_text())["traps"]}
    except (OSError, KeyError, TypeError, yaml.YAMLError) as error:
        raise InvalidDataset(f"{traps_path.name}: {error}") from error
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise InvalidDataset(f"{path.name}: duplicate case ids")
    for case in cases:
        where = f"{path.name}: case {case.id}"
        for key in case.relevant_keys + case.forbidden_keys:
            if key not in seed:
                raise InvalidDataset(f"{where} names unknown memory {key}")
        if set(case.relevant_keys) & set(case.forbidden_keys):
            raise InvalidDataset(f"{where} lists a memory as both relevant and forbidden")
        if case.expect_no_answer == bool(case.relevant_keys):
            raise InvalidDataset(
                f"{where}: relevant_keys must be empty exactly when expect_no_answer is set"
            )
        for key in case.relevant_keys:
            reason = why_not_returnable(seed[key], case)
            if reason:
                raise InvalidDataset(f"{where}: relevant memory {key} {reason}")
        if case.trap_id is not None and case.trap_id not in trap_ids:
            raise InvalidDataset(f"{where} names unknown trap {case.trap_id}")
    return cases


def load_all() -> dict[str, list[RetrievalCase]]:
    """Both splits, checked against each other: ids and questions must not repeat."""
    splits = {split: load_cases(split) for split in SPLITS}
    every = [case for cases in splits.values() for case in cases]
    ids = [case.id for case in every]
    if len(ids) != len(set(ids)):
        raise InvalidDataset("a case id appears more than once across the two splits")
    # The same words may be asked twice with different flags; the same ask must not repeat
    asks = [(c.question, c.include_external, c.include_upcoming) for c in every]
    if len(asks) != len(set(asks)):
        raise InvalidDataset("a question appears more than once across the two splits")
    return splits
