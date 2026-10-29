"""Agent eval cases: the schema and the loader.

Each row of ``datasets/agent.dev.jsonl`` and ``.test.jsonl`` is one scenario played through the
agent loop on the seeded memories (``data/seed_memories.yaml``): optional earlier owner messages in
the same session, then the message that is judged. Memories are named by their seed key.

A row says what must hold, and the runner checks it in code:

- ``must_recall``: seed keys that must be among the memories the loop recalled for the judged message.
- ``must_not_recall``: seed keys the loop may never recall. Every one is a memory that search may
  not return, so a hit is a leak, as in the retrieval eval.

A ``history`` case asks about the past ("where did I live before?"). For these the loop also recalls
memories that were replaced and events that have ended, so ``must_recall`` may name such a memory,
and at least one must be of that kind. ``must_not_recall`` then names memories that no recall may
return: deleted, external or not yet started.
- ``must_include``: groups of words. Every group needs at least one of its words in the answer, so
  ``[["14 February", "February 14"]]`` accepts either wording.
- ``must_not_include``: words that may not appear in the answer.
- ``expect_unknown``: the answer must say it does not know.

Matching is case-insensitive on the answer text. The loader checks every row against the seed
file, so a mistake in the data is caught here and not as a strange score later.
"""

import json
from pathlib import Path
from typing import Literal

import yaml
from memory_cases import EVAL_NOW, InvalidDataset
from memory_retrieval_cases import SEED, TRAPS, RetrievalCase, load_seed_index, why_not_returnable
from pydantic import BaseModel, ConfigDict, ValidationError

from app.memory.seed import SeedMemory

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"

SPLITS = ("dev", "test")

Category = Literal[
    "recall_used",
    "changed_fact",
    "forgotten",
    "stale_event",
    "irrelevant",
    "false_memory",
    "follow_up",
    "duplicate_wording",
    "injection",
    "history",
]

# A case only asks what the agent's default recall allows: owner memories, active, in force.
DEFAULT_FLAGS = RetrievalCase(
    id="-",
    category="direct",
    question="-",
    relevant_keys=[],
    forbidden_keys=[],
    include_external=False,
    include_upcoming=False,
    expect_no_answer=True,
    trap_id=None,
    note=None,
)


class AgentCase(BaseModel):
    """Every field is required, with an empty value where it does not apply, so all rows look alike."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    prior_turns: list[str]
    message: str
    must_recall: list[str]
    must_not_recall: list[str]
    must_include: list[list[str]]
    must_not_include: list[str]
    expect_unknown: bool
    trap_id: str | None
    note: str | None


def is_past(memory: SeedMemory) -> bool:
    """A memory only a history question may recall: replaced, or an event that has ended."""
    if memory.source_kind != "owner" or memory.status == "deleted":
        return False
    if memory.valid_from and memory.valid_from > EVAL_NOW:
        return False
    if memory.status == "superseded":
        return True
    return memory.valid_until is not None and memory.valid_until <= EVAL_NOW


def load_cases(
    split: str,
    directory: Path = DATASETS,
    seed_path: Path = SEED,
    traps_path: Path = TRAPS,
) -> list[AgentCase]:
    """The cases of a split. An empty file is a valid, empty split."""
    if split not in SPLITS:
        raise InvalidDataset(f"unknown split {split!r}; expected one of {SPLITS}")
    path = directory / f"agent.{split}.jsonl"
    cases: list[AgentCase] = []
    try:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.strip():
                try:
                    cases.append(AgentCase.model_validate(json.loads(line)))
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
        for key in case.must_recall + case.must_not_recall:
            if key not in seed:
                raise InvalidDataset(f"{where} names unknown memory {key}")
        if set(case.must_recall) & set(case.must_not_recall):
            raise InvalidDataset(f"{where} lists a memory as both recalled and not recalled")
        history = case.category == "history"
        for key in case.must_recall:
            reason = why_not_returnable(seed[key], DEFAULT_FLAGS)
            if reason and not (history and is_past(seed[key])):
                raise InvalidDataset(f"{where}: must_recall memory {key} {reason}")
        if history and not any(is_past(seed[key]) for key in case.must_recall):
            raise InvalidDataset(f"{where}: a history case must recall a replaced or ended memory")
        for key in case.must_not_recall:
            if why_not_returnable(seed[key], DEFAULT_FLAGS) is None:
                raise InvalidDataset(
                    f"{where}: must_not_recall memory {key} is one the agent may recall"
                )
            if history and is_past(seed[key]):
                raise InvalidDataset(
                    f"{where}: must_not_recall memory {key} is one a history question may recall"
                )
        if any(not group or not all(word.strip() for word in group) for group in case.must_include):
            raise InvalidDataset(f"{where}: must_include has an empty group or word")
        if not all(word.strip() for word in case.must_not_include):
            raise InvalidDataset(f"{where}: must_not_include has an empty word")
        if not case.message.strip() or not all(turn.strip() for turn in case.prior_turns):
            raise InvalidDataset(f"{where}: a message is empty")
        required = {word.lower() for group in case.must_include for word in group}
        if required & {word.lower() for word in case.must_not_include}:
            raise InvalidDataset(f"{where}: a word is both required and forbidden")
        if case.expect_unknown and case.must_include:
            raise InvalidDataset(f"{where}: an answer that says it does not know has no facts")
        if case.category == "follow_up" and not case.prior_turns:
            raise InvalidDataset(f"{where}: a follow_up needs earlier turns")
        if not (
            case.must_recall
            or case.must_not_recall
            or case.must_include
            or case.must_not_include
            or case.expect_unknown
        ):
            raise InvalidDataset(f"{where} checks nothing")
        if case.trap_id is not None and case.trap_id not in trap_ids:
            raise InvalidDataset(f"{where} names unknown trap {case.trap_id}")
    return cases


def load_all() -> dict[str, list[AgentCase]]:
    """Both splits, checked against each other: ids and messages must not repeat."""
    splits = {split: load_cases(split) for split in SPLITS}
    every = [case for cases in splits.values() for case in cases]
    ids = [case.id for case in every]
    if len(ids) != len(set(ids)):
        raise InvalidDataset("a case id appears more than once across the two splits")
    asks = [(tuple(case.prior_turns), case.message) for case in every]
    if len(asks) != len(set(asks)):
        raise InvalidDataset("a message appears more than once across the two splits")
    return splits
