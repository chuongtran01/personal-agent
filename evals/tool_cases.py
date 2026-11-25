"""Tool eval cases: the schema and the loader.

Each row of ``datasets/tools.dev.jsonl`` and ``.test.jsonl`` is one scenario played through the agent
loop with the read tools of phase 6 over the fake mailbox and calendar (``data/fake_mailbox.yaml``,
``data/fake_calendar.yaml``): optional earlier owner messages in the same chat, then the message that
is judged. The seeded memories are there too, and the clock is ``EVAL_NOW``, a Wednesday.

A row says what must hold, and the runner checks it in code:

- ``expect_calls``: calls the agent must make. Each names a tool and says what the arguments must
  amount to, not how they are spelled:
  ``calendar_list_events`` has ``covers`` (two dates the queried range must include) and ``max_days``
  (it may not ask for more days than that); ``email_search`` has ``words`` (one of them must be in
  the query or the sender) and ``since`` (the earliest and latest date it may start from);
  ``email_read`` has the ``id`` to read. An empty list means no tool is needed.
- ``max_calls``: the most calls the turn may make. With nothing expected, 0 means "do not look".
- ``forbidden_ids``: messages the agent may not read, however it was asked to.
- ``must_include`` / ``must_not_include``: groups of words and single words, as in the agent eval.
- ``expect_empty``: the answer must say there is nothing.
- ``failure``: every tool call fails (``down``, ``slow`` or ``refused``) and the answer must say it
  could not look.

Every case must also leave memory unchanged: nothing the owner says in these cases is worth keeping,
so a change is an invented or injected memory.

The loader checks every row against the fixtures, so a mistake in the data is caught here and not as
a strange score later.
"""

import json
from datetime import date
from pathlib import Path
from typing import Literal

from memory_cases import InvalidDataset
from pydantic import BaseModel, ConfigDict, ValidationError
from tool_fixtures import SPECS, load_messages

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"

SPLITS = ("dev", "test")

Category = Literal[
    "calendar",
    "email",
    "combined",
    "no_tool",
    "empty",
    "cut",
    "failure",
    "injection",
    "memory_and_tool",
]

TOOLS = {spec.name for spec in SPECS}


class ExpectedCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    covers: list[str] | None = None
    max_days: int | None = None
    words: list[str] | None = None
    since: list[str] | None = None
    id: str | None = None


class ToolCase(BaseModel):
    """Every field is required, with an empty value where it does not apply, so all rows look alike."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    prior_turns: list[str]
    message: str
    expect_calls: list[ExpectedCall]
    max_calls: int
    forbidden_ids: list[str]
    must_include: list[list[str]]
    must_not_include: list[str]
    expect_empty: bool
    failure: Literal["down", "slow", "refused"] | None
    note: str | None


def _days(values: list[str] | None, where: str, count: int) -> list[date]:
    if values is None or len(values) != count:
        raise InvalidDataset(f"{where} needs {count} dates")
    try:
        days = [date.fromisoformat(v) for v in values]
    except ValueError as error:
        raise InvalidDataset(f"{where}: {error}") from error
    if days != sorted(days):
        raise InvalidDataset(f"{where}: dates must be in order")
    return days


def _check_call(call: ExpectedCall, where: str, message_ids: set[str]) -> None:
    if call.tool not in TOOLS:
        raise InvalidDataset(f"{where}: unknown tool {call.tool}")
    where = f"{where} ({call.tool})"
    if call.tool == "calendar_list_events":
        first, last = _days(call.covers, f"{where}: covers", 2)
        if call.max_days is None or call.max_days < (last - first).days + 1:
            raise InvalidDataset(f"{where}: max_days must allow the days it covers")
        if call.words or call.since or call.id:
            raise InvalidDataset(f"{where}: only covers and max_days apply to a calendar call")
    elif call.tool == "email_search":
        if call.since is not None:
            _days(call.since, f"{where}: since", 2)
        if call.words is not None and not all(w.strip() for w in call.words):
            raise InvalidDataset(f"{where}: an empty word")
        if call.covers or call.max_days or call.id:
            raise InvalidDataset(f"{where}: only words and since apply to a search")
    else:
        if call.id not in message_ids:
            raise InvalidDataset(f"{where}: unknown message {call.id}")
        if call.covers or call.max_days or call.words or call.since:
            raise InvalidDataset(f"{where}: only id applies to a read")


def load_cases(split: str, directory: Path = DATASETS) -> list[ToolCase]:
    """The cases of a split. An empty file is a valid, empty split."""
    if split not in SPLITS:
        raise InvalidDataset(f"unknown split {split!r}; expected one of {SPLITS}")
    path = directory / f"tools.{split}.jsonl"
    cases: list[ToolCase] = []
    try:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.strip():
                try:
                    cases.append(ToolCase.model_validate(json.loads(line)))
                except (json.JSONDecodeError, ValidationError) as error:
                    raise InvalidDataset(f"{path.name} line {number}: {error}") from error
    except OSError as error:
        raise InvalidDataset(f"{path.name}: {error}") from error

    message_ids = {m.id for m in load_messages()}
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise InvalidDataset(f"{path.name}: duplicate case ids")
    for case in cases:
        where = f"{path.name}: case {case.id}"
        for call in case.expect_calls:
            _check_call(call, where, message_ids)
        for message_id in case.forbidden_ids:
            if message_id not in message_ids:
                raise InvalidDataset(f"{where}: forbidden_ids names unknown message {message_id}")
        read = {c.id for c in case.expect_calls if c.tool == "email_read"}
        if read & set(case.forbidden_ids):
            raise InvalidDataset(f"{where}: a message is both read and forbidden")
        if case.max_calls < len(case.expect_calls):
            raise InvalidDataset(f"{where}: max_calls is below the calls it expects")
        if any(not group or not all(word.strip() for word in group) for group in case.must_include):
            raise InvalidDataset(f"{where}: must_include has an empty group or word")
        if not all(word.strip() for word in case.must_not_include):
            raise InvalidDataset(f"{where}: must_not_include has an empty word")
        if not case.message.strip() or not all(turn.strip() for turn in case.prior_turns):
            raise InvalidDataset(f"{where}: a message is empty")
        required = {word.lower() for group in case.must_include for word in group}
        if required & {word.lower() for word in case.must_not_include}:
            raise InvalidDataset(f"{where}: a word is both required and forbidden")
        if case.expect_empty and case.must_include:
            raise InvalidDataset(f"{where}: an answer that finds nothing has no facts to include")
        if case.failure and not case.expect_calls:
            raise InvalidDataset(f"{where}: a failure case must expect the call that fails")
        if case.failure and case.expect_empty:
            raise InvalidDataset(f"{where}: a failed look is not an empty result")
        if case.category == "no_tool" and (case.expect_calls or case.max_calls != 0):
            raise InvalidDataset(f"{where}: a no_tool case expects no calls and allows none")
        if case.category == "failure" and not case.failure:
            raise InvalidDataset(f"{where}: a failure case needs a failure")
        if case.failure and case.category != "failure":
            raise InvalidDataset(f"{where}: only a failure case sets failure")
        if case.category == "injection" and not (case.must_not_include or case.forbidden_ids):
            raise InvalidDataset(f"{where}: an injection case must forbid something")
        if case.category == "empty" and not case.expect_empty:
            raise InvalidDataset(f"{where}: an empty case must expect an empty answer")
        if not (
            case.expect_calls
            or case.max_calls == 0
            or case.must_include
            or case.must_not_include
            or case.expect_empty
            or case.forbidden_ids
        ):
            raise InvalidDataset(f"{where} checks nothing")
    return cases


def load_all() -> dict[str, list[ToolCase]]:
    """Both splits, checked against each other: ids and messages must not repeat."""
    splits = {split: load_cases(split) for split in SPLITS}
    every = [case for cases in splits.values() for case in cases]
    ids = [case.id for case in every]
    if len(ids) != len(set(ids)):
        raise InvalidDataset("a case id appears more than once across the two splits")
    # The same message may be asked again with a failing tool
    asks = [(tuple(case.prior_turns), case.message, case.failure) for case in every]
    if len(asks) != len(set(asks)):
        raise InvalidDataset("a message appears more than once across the two splits")
    return splits
