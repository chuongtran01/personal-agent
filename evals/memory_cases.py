"""Memory eval cases: the schema and the loader.

A case is a scripted conversation played against one database that starts empty: some sessions
(owner turns, with optional external content such as an email, and the days since the previous
session), then a probe asked in a new session ``probe_gap_days`` after the last one. The probe is
asked at ``EVAL_NOW``, and the sessions are placed on the clock backwards from it, so "next Friday"
or "3 March" means the same on every day the eval runs. The runner checks the probe's reply, and
the memories written, with code, not a judge:

- ``must_include``: every one of these words appears in the reply (case-insensitive).
- ``must_not_include``: none of these words appears in the reply.
- ``expected_behavior``: ``answer`` (it is answered), ``not_know`` (the reply says it does not
  know), or ``ignore`` (a stored fact that has nothing to do with the question is not brought up).
- ``must_not_store``: none of these words appears in any memory the agent wrote, whatever its
  status, read from the table. It is how a secret or an injected instruction is caught even when
  the reply happens not to repeat it.
- ``reply_must_not_misstate`` (on a session): the assistant's replies in that session neither say
  it cannot do what was asked nor claim that it is already done.

``data/memory_traps.yaml`` is the ground truth for the traps; every case that names a
``trap_id`` must name one that exists there.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
TRAPS = ROOT.parent / "data" / "memory_traps.yaml"

SPLITS = ("dev", "test")

# The clock the retrieval eval runs on, so "already happened" does not move with the real date
EVAL_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

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
    # None of this session's replies may misstate what the assistant does. For a request to forget:
    # it can ("I can't delete that" is false), and the memory is updated after the reply, so "I've
    # deleted it" is premature. The right thing to say is that it will stop using it.
    reply_must_not_misstate: bool = False


def _problem(
    sessions: list[Session],
    expected_behavior: str,
    must_include: list[str],
    must_not_include: list[str],
    must_not_store: list[str],
) -> str | None:
    """What is inconsistent in a case or a trap, or None. Both have the same shape."""
    if sessions[0].gap_days != 0:
        return "the first session has no previous session, so its gap_days must be 0"
    words = must_include + must_not_include + must_not_store
    if not all(word.strip() for word in words):
        return "a word to check is empty"
    if not (must_include or must_not_include or must_not_store or expected_behavior == "not_know"):
        return "checks nothing"
    if expected_behavior == "not_know" and must_include:
        return "an answer that says it does not know has no facts to include"
    if expected_behavior == "answer" and not must_include:
        # Without a word the reply must contain, a reply that knows nothing could pass
        return "an answer case must name a word the reply must include"
    if {w.casefold() for w in must_include} & {w.casefold() for w in must_not_include}:
        return "a word is both required and forbidden in the reply"
    if any(not turn.strip() for session in sessions for turn in session.turns):
        return "a turn is empty"
    return None


class MemoryCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    description: str = ""
    sessions: list[Session] = Field(min_length=1)
    # Days between the last session and the probe; the probe is asked at EVAL_NOW
    probe_gap_days: int = Field(default=0, ge=0)
    probe: str
    expected_behavior: Behavior
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    must_not_store: list[str] = Field(default_factory=list)
    trap_id: str | None = None

    @model_validator(mode="after")
    def _consistent(self):
        problem = _problem(
            self.sessions,
            self.expected_behavior,
            self.must_include,
            self.must_not_include,
            self.must_not_store,
        )
        if problem:
            raise ValueError(f"case {self.id}: {problem}")
        if not self.probe.strip():
            raise ValueError(f"case {self.id}: the probe is empty")
        return self


class Trap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    sessions: list[Session] = Field(min_length=1)
    probe_gap_days: int = Field(default=0, ge=0)
    probe: str
    expected_behavior: Behavior
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    must_not_store: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self):
        problem = _problem(
            self.sessions,
            self.expected_behavior,
            self.must_include,
            self.must_not_include,
            self.must_not_store,
        )
        if problem:
            raise ValueError(f"trap {self.id}: {problem}")
        return self


def session_times(
    sessions: list[Session], probe_gap_days: int, probe_at: datetime = EVAL_NOW
) -> list[datetime]:
    """When each session happens, so that the probe at ``probe_at`` comes ``probe_gap_days`` after
    the last one and each session comes ``gap_days`` after the one before it."""
    times = [probe_at - timedelta(days=probe_gap_days)]
    for session in reversed(sessions[1:]):
        times.append(times[-1] - timedelta(days=session.gap_days))
    return list(reversed(times))


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
