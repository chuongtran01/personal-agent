"""Extraction eval cases: the schema and the loader.

Each row of ``datasets/extraction.dev.jsonl`` and ``.test.jsonl`` is one owner turn given to the
memory-writing step, together with the memories that already exist. There is no agent, no
database and no search: the step under test gets the case and answers with the changes it would
make, and ``extraction_checks`` judges those changes in code.

A case holds:

- ``existing``: memories already stored, each with a case-local ``key`` the changes refer to. A
  superseded or deleted one is there to show that it is never touched again.
- ``context``: earlier messages of the chat, for a turn that only makes sense with them.
- ``owner``: the owner's messages of this turn. These are the only source of a memory.
- ``reply``: what the assistant answered. It is shown as context and is never a source.

and what must hold afterwards:

- ``writes``: new memories that must exist. Each names words (``include`` is groups, and every
  group needs one of its words), and optionally a kind, the memory it ``replaces`` and the dates
  of an event. A write with no ``replaces`` is a new memory.
- ``deletes``: existing keys that must end up deleted.
- ``no_changes``: nothing at all may change.
- ``must_not_store``: words that may not appear in any stored text.

Every change not asked for is a failure, and one that touches a memory the owner did not mean
(a wrong delete or a wrong replacement) or stores a forbidden word is a zero-tolerance one.
Dates are read in the owner's time zone (``EVAL_TZ``) with the clock at ``EVAL_NOW``, which is a
Wednesday, so "this Friday" means the same on every day the eval runs.
"""

import json
from datetime import date
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from memory_cases import EVAL_NOW, InvalidDataset
from pydantic import BaseModel, ConfigDict, ValidationError

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"

SPLITS = ("dev", "test")

EVAL_TZ = ZoneInfo("Europe/Lisbon")

__all__ = ["EVAL_NOW", "EVAL_TZ", "InvalidDataset"]

Category = Literal[
    "add",
    "event_date",
    "no_write",
    "assistant_claim",
    "duplicate",
    "supersede",
    "delete",
    "delete_unclear",
    "sensitive",
    "multi_fact",
    "non_english",
]
Kind = Literal["fact", "preference", "event", "episode"]
Status = Literal["active", "superseded", "deleted"]
Role = Literal["owner", "assistant"]


class Existing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    kind: Kind
    content: str
    status: Status


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    text: str


class Write(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind | None
    include: list[list[str]]
    replaces: str | None
    starts: date | None  # the day an event starts, in the owner's time zone
    ends: date | None  # the last day; an end stored as the start of the next day also counts


class ExtractionCase(BaseModel):
    """Every field is required, with an empty value where it does not apply, so all rows look alike."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    existing: list[Existing]
    context: list[Message]
    owner: list[str]
    reply: str | None
    writes: list[Write]
    deletes: list[str]
    no_changes: bool
    must_not_store: list[str]
    note: str | None


def _problem(case: ExtractionCase) -> str | None:
    """What is wrong with one row, or None."""
    by_key = {memory.key: memory for memory in case.existing}
    if len(by_key) != len(case.existing):
        return "an existing memory key appears twice"
    if not case.owner or not all(text.strip() for text in case.owner):
        return "an owner message is empty"
    if not all(m.text.strip() for m in case.context):
        return "a context message is empty"
    if case.reply is not None and not case.reply.strip():
        return "the reply is empty"
    if any(not memory.content.strip() for memory in case.existing):
        return "an existing memory is empty"

    replaced = [write.replaces for write in case.writes if write.replaces]
    if len(replaced) != len(set(replaced)):
        return "two writes replace the same memory"
    for key in replaced + case.deletes:
        if key not in by_key:
            return f"names unknown memory {key}"
        if by_key[key].status != "active":
            return f"names memory {key}, which is not active, so it cannot be changed"
    if len(case.deletes) != len(set(case.deletes)):
        return "a memory is deleted twice"
    if set(replaced) & set(case.deletes):
        return "a memory is both replaced and deleted"

    for write in case.writes:
        if not write.include:
            return "a write names no words"
        if any(not group or not all(word.strip() for word in group) for group in write.include):
            return "a write has an empty group or word"
        if write.starts and write.ends and write.ends < write.starts:
            return "an event ends before it starts"
    if not all(word.strip() for word in case.must_not_store):
        return "must_not_store has an empty word"
    required = {word.casefold() for write in case.writes for g in write.include for word in g}
    if required & {word.casefold() for word in case.must_not_store}:
        return "a word is both required and forbidden"

    if case.no_changes and (case.writes or case.deletes):
        return "no_changes with changes asked for"
    if not (case.no_changes or case.writes or case.deletes):
        return "checks nothing: ask for a change or set no_changes"
    if case.category == "delete" and not case.deletes:
        return "a delete case must delete something"
    if case.category == "supersede" and not any(w.replaces for w in case.writes):
        return "a supersede case must replace something"
    if case.category == "event_date" and not any(w.starts for w in case.writes):
        return "an event_date case must give a start date"
    if case.category == "multi_fact" and len(case.writes) < 2:
        return "a multi_fact case needs at least two writes"
    if case.category in {"no_write", "assistant_claim", "delete_unclear"} and not case.no_changes:
        return f"a {case.category} case changes nothing"
    if case.category in {"assistant_claim", "sensitive"} and not case.must_not_store:
        return f"a {case.category} case names what must not be stored"
    if case.category == "assistant_claim" and not case.reply:
        return "an assistant_claim case needs a reply"
    return None


def load_cases(split: str, directory: Path = DATASETS) -> list[ExtractionCase]:
    """The cases of a split. An empty file is a valid, empty split."""
    if split not in SPLITS:
        raise InvalidDataset(f"unknown split {split!r}; expected one of {SPLITS}")
    path = directory / f"extraction.{split}.jsonl"
    cases: list[ExtractionCase] = []
    try:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.strip():
                try:
                    cases.append(ExtractionCase.model_validate(json.loads(line)))
                except (json.JSONDecodeError, ValidationError) as error:
                    raise InvalidDataset(f"{path.name} line {number}: {error}") from error
    except OSError as error:
        raise InvalidDataset(f"{path.name}: {error}") from error
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise InvalidDataset(f"{path.name}: duplicate case ids")
    for case in cases:
        problem = _problem(case)
        if problem:
            raise InvalidDataset(f"{path.name}: case {case.id} {problem}")
    return cases


def load_all(directory: Path = DATASETS) -> dict[str, list[ExtractionCase]]:
    """Both splits, checked against each other: ids and inputs must not repeat."""
    splits = {split: load_cases(split, directory) for split in SPLITS}
    every = [case for cases in splits.values() for case in cases]
    ids = [case.id for case in every]
    if len(ids) != len(set(ids)):
        raise InvalidDataset("a case id appears more than once across the two splits")
    asks = [
        (tuple(case.owner), case.reply, tuple((m.role, m.text) for m in case.context))
        for case in every
    ]
    if len(asks) != len(set(asks)):
        raise InvalidDataset("the same input appears more than once across the two splits")
    return splits
