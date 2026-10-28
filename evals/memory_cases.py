"""Memory eval cases: the schema and the loader.

A case is a scripted conversation played against one database: some sessions (owner turns, with
optional external content such as an email, and a gap in days before the session), then a probe
asked in a new session. The runner checks the probe's reply with code, not a judge.

``data/memory_traps.yaml`` is the ground truth for the traps; every case that names a
``trap_id`` must name one that exists there.
"""

import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
TRAPS = ROOT.parent / "data" / "memory_traps.yaml"

SPLITS = ("dev", "test")

Category = Literal[
    "recall", "update", "forgetting", "relevance", "stale", "sensitive", "duplicate", "injection"
]
Behavior = Literal["answer", "not_know", "ignore"]


class InvalidDataset(ValueError):
    """The dataset files are malformed or inconsistent."""


class External(BaseModel):
    """Content that comes from outside the owner: an email or a calendar event."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["email", "event"]
    text: str


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: list[str] = Field(min_length=1)
    external: list[External] = Field(default_factory=list)
    # Days since the previous session; the runner moves the clock, so nothing sleeps
    gap_days: int = Field(default=0, ge=0)


class MemoryCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    description: str = ""
    sessions: list[Session] = Field(min_length=1)
    probe: str
    expected_behavior: Behavior
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    trap_id: str | None = None

    @model_validator(mode="after")
    def _has_a_check(self):
        if not (self.must_include or self.must_not_include or self.expected_behavior == "not_know"):
            raise ValueError(f"case {self.id} checks nothing")
        return self


class Trap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    sessions: list[Session] = Field(min_length=1)
    probe: str
    expected_behavior: Behavior
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    notes: str = ""


def load_traps(path: Path = TRAPS) -> list[Trap]:
    try:
        rows = yaml.safe_load(path.read_text())["traps"]
        traps = [Trap.model_validate(row) for row in rows]
    except (OSError, KeyError, TypeError, ValidationError, yaml.YAMLError) as error:
        raise InvalidDataset(f"{path.name}: {error}") from error
    ids = [trap.id for trap in traps]
    if len(ids) != len(set(ids)):
        raise InvalidDataset(f"{path.name}: duplicate trap ids")
    return traps


def load_cases(
    split: str, directory: Path = DATASETS, traps_path: Path = TRAPS
) -> list[MemoryCase]:
    """The cases of a split. An empty file is a valid, empty split."""
    if split not in SPLITS:
        raise InvalidDataset(f"unknown split {split!r}; expected one of {SPLITS}")
    path = directory / f"memory.{split}.jsonl"
    cases: list[MemoryCase] = []
    try:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.strip():
                try:
                    cases.append(MemoryCase.model_validate(json.loads(line)))
                except (json.JSONDecodeError, ValidationError) as error:
                    raise InvalidDataset(f"{path.name} line {number}: {error}") from error
    except OSError as error:
        raise InvalidDataset(f"{path.name}: {error}") from error
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise InvalidDataset(f"{path.name}: duplicate case ids")
    known = {trap.id for trap in load_traps(traps_path)}
    for case in cases:
        if case.trap_id is not None and case.trap_id not in known:
            raise InvalidDataset(f"{path.name}: case {case.id} names unknown trap {case.trap_id}")
    return cases
