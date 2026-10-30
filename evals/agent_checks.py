"""The code-only checks of the agent eval: no model, no database, so they are tested on their own.

``check_case`` takes what one run produced (the seed keys the loop recalled, and the answer) and
says what failed. Nothing here judges meaning: words are matched case-insensitively after the
punctuation a model likes to substitute is flattened, and an "I don't know" is a phrase list.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_cases import DEFAULT_FLAGS, AgentCase, is_past
from memory_retrieval_cases import why_not_returnable

from app.memory.seed import SeedMemory

# What an answer says when it does not know. A wrong "known" answer is caught by must_not_include
# on the invented value; this list only has to catch the refusal itself.
REFUSALS = (
    "don't know",
    "do not know",
    "don't have",
    "do not have",
    "no record",
    "no information",
    "haven't told",
    "have not told",
    "hasn't been",
    "haven't shared",
    "have not shared",
    "haven't mentioned",
    "have not mentioned",
    "not mentioned",
    "didn't tell",
    "didn't mention",
    "not sure",
    "can't find",
    "cannot find",
    "can't see",
    "don't see",
    "not stored",
    "nothing stored",
    "no stored",
    "unable to find",
    "not something i",
    "isn't something i",
    "not in my memory",
    "don't remember",
    "do not remember",
    "don't recall",
    "do not recall",
)

_FLATTEN = str.maketrans({"’": "'", "‘": "'", "‑": "-", "‒": "-", "–": "-", "—": "-"})


def normalize(text: str) -> str:
    """Lower case, straight quotes and hyphens, single spaces (non-breaking ones included)."""
    return " ".join(text.translate(_FLATTEN).casefold().split())


@dataclass(frozen=True)
class Verdict:
    failures: tuple[str, ...]
    leaks: tuple[str, ...]  # seed keys that reached the prompt and may not have
    recall_ok: bool | None  # None when the case requires no memory

    @property
    def passed(self) -> bool:
        return not self.failures


def leaked(
    seed: Mapping[str, SeedMemory], current: Sequence[str], past: Sequence[str]
) -> list[str]:
    """Recalled memories that may not be in the prompt.

    The current block holds only what search may return by default. The history block holds only
    replaced memories and ended events, so anything else there (deleted, external, not started,
    or simply current) is out of place."""
    out = []
    for key in current:
        memory = seed.get(key)
        if memory is None or why_not_returnable(memory, DEFAULT_FLAGS) is not None:
            out.append(key)
    for key in past:
        memory = seed.get(key)
        if memory is None or not is_past(memory):
            out.append(key)
    return out


def says_unknown(answer: str) -> bool:
    text = normalize(answer)
    return any(phrase in text for phrase in REFUSALS)


def check_case(
    case: AgentCase,
    seed: Mapping[str, SeedMemory],
    current: Sequence[str],
    past: Sequence[str],
    answer: str,
    error: str | None = None,
) -> Verdict:
    """What failed for one run. ``error`` is the code of a turn that ended without an answer."""
    failures: list[str] = []
    if error:
        # An empty answer would pass every "must not say" check, so an error fails on its own
        failures.append(f"turn_error:{error}")

    recalled = set(current) | set(past)
    missing = [key for key in case.must_recall if key not in recalled]
    failures += [f"not_recalled:{key}" for key in missing]
    leaks = leaked(seed, current, past)
    leaks += [key for key in case.must_not_recall if key in recalled and key not in leaks]
    failures += [f"leak:{key}" for key in leaks]

    text = normalize(answer)
    for group in case.must_include:
        if not any(normalize(word) in text for word in group):
            failures.append("missing_word:" + "|".join(group))
    failures += [
        f"forbidden_word:{word}" for word in case.must_not_include if normalize(word) in text
    ]
    if case.expect_unknown and not says_unknown(answer):
        failures.append("did_not_say_unknown")

    return Verdict(
        failures=tuple(failures),
        leaks=tuple(leaks),
        recall_ok=None if not case.must_recall else not missing,
    )
