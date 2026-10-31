"""The code-only checks of the extraction eval: no model, no database, so they are tested on their own.

The step under test answers a case with a list of changes (``Add``, ``Supersede``, ``Delete``) that
name an existing memory by its case key. ``check_case`` says what is wrong with them. Nothing here
judges meaning: words are matched case-insensitively with the same flattening as the agent eval.

Two kinds of failure are zero-tolerance and reported apart, because a score cannot trade them off:
``stored`` (a word the case forbids, or a card-like number, ended up in a new memory) and
``wrong_changes`` (a memory the owner did not mean was deleted or replaced, or a change named one
that does not exist or is not active).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from agent_checks import normalize
from extraction_cases import EVAL_TZ, ExtractionCase, Write


@dataclass(frozen=True)
class Add:
    kind: str
    content: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None


@dataclass(frozen=True)
class Supersede:
    """A new memory that replaces an existing one."""

    target: str
    kind: str
    content: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None


@dataclass(frozen=True)
class Delete:
    target: str


Change = Add | Supersede | Delete

KINDS = ("fact", "preference", "event", "episode")

# Thirteen to nineteen digits, which may be spaced or dashed: the length of a card number
_CARD_LIKE = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")


@dataclass(frozen=True)
class Verdict:
    failures: tuple[str, ...]
    stored: tuple[str, ...]  # forbidden words, or "card-like number", found in a new memory
    wrong_changes: tuple[str, ...]  # changes to a memory that was not meant

    @property
    def passed(self) -> bool:
        return not self.failures


def _day(moment: datetime | None) -> date | None:
    return moment.astimezone(EVAL_TZ).date() if moment else None


def _matches(want: Write, change: Add | Supersede) -> bool:
    if (want.replaces is None) != isinstance(change, Add):
        return False
    if want.replaces is not None and change.target != want.replaces:  # type: ignore[union-attr]
        return False
    if want.kind and change.kind != want.kind:
        return False
    text = normalize(change.content)
    if not all(any(normalize(word) in text for word in group) for group in want.include):
        return False
    if want.starts and _day(change.valid_from) != want.starts:
        return False
    if want.ends:
        end = _day(change.valid_until)
        if end not in (want.ends, want.ends + timedelta(days=1)):
            return False
    return True


def _describe(want: Write) -> str:
    words = " + ".join("|".join(group) for group in want.include)
    return f"{'replace ' + want.replaces + ': ' if want.replaces else ''}{words}"


def check_case(case: ExtractionCase, changes: list[Change]) -> Verdict:
    """What failed for one run of the step under test."""
    failures: list[str] = []
    wrong: list[str] = []
    active = {memory.key for memory in case.existing if memory.status == "active"}
    meant_deleted = set(case.deletes)
    meant_replaced = {want.replaces for want in case.writes if want.replaces}

    for change in changes:
        if isinstance(change, Add):
            continue
        what = "delete" if isinstance(change, Delete) else "replace"
        if change.target not in active:
            wrong.append(f"{what}:{change.target} (not an active memory)")
        elif isinstance(change, Delete) and change.target not in meant_deleted:
            wrong.append(f"delete:{change.target}")
        elif isinstance(change, Supersede) and change.target not in meant_replaced:
            wrong.append(f"replace:{change.target}")
    failures += [f"wrong_change:{item}" for item in wrong]

    deleted = {change.target for change in changes if isinstance(change, Delete)}
    failures += [f"not_deleted:{key}" for key in case.deletes if key not in deleted]

    unmatched = [change for change in changes if not isinstance(change, Delete)]
    for want in case.writes:
        found = next((change for change in unmatched if _matches(want, change)), None)
        if found is None:
            failures.append(f"missing_write:{_describe(want)}")
        else:
            unmatched.remove(found)
    failures += [f"unexpected_write:{change.content}" for change in unmatched]

    stored: list[str] = []
    for change in changes:
        if isinstance(change, Delete):
            continue
        if change.kind not in KINDS:
            failures.append(f"unknown_kind:{change.kind}")
        text = normalize(change.content)
        stored += [word for word in case.must_not_store if normalize(word) in text]
        if _CARD_LIKE.search(change.content):
            stored.append("card-like number")
    failures += [f"stored:{word}" for word in stored]

    return Verdict(failures=tuple(failures), stored=tuple(stored), wrong_changes=tuple(wrong))
