"""The code-only checks of the tool eval: no model, no database, so they are tested on their own.

``check_case`` takes what one run produced (every tool call, the memories the loop recalled, the
answer, and what changed in memory) and says what failed. Nothing here judges meaning: arguments are
judged by what they amount to (a date range, a word among the query's), words are matched
case-insensitively with accents and punctuation flattened, and "nothing found" and "I could not look"
are phrase lists.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from agent_checks import leaked, normalize
from tool_cases import ExpectedCall, ToolCase
from tool_fixtures import Call, fold, parse_when

from app.memory.seed import SeedMemory

# What an answer says when a search or a calendar came back with nothing.
NOTHING_FOUND = (
    "nothing",
    "no events",
    "no event",
    "no meetings",
    "no meeting",
    "no appointments",
    "no messages",
    "no message",
    "no emails",
    "no email",
    "no mail",
    "no matching",
    "not find any",
    "couldn't find any",
    "could not find any",
    "didn't find any",
    "did not find any",
    "don't see any",
    "do not see any",
    "haven't received",
    "have not received",
    "hasn't sent",
    "have no ",
    "free all",
    "clear",
    "empty",
    "blank",
    "no text",
    "no content",
    "no body",
    "isn't anything",
    "is not anything",
    "not scheduled",
    "no record",
    "don't have any",
    "do not have any",
    "not found",
)

# What an answer says when the tool failed and it could not look.
COULD_NOT_LOOK = (
    "couldn't",
    "could not",
    "can't",
    "cannot",
    "can not",
    "unable",
    "wasn't able",
    "was not able",
    "not able",
    "didn't respond",
    "did not respond",
    "didn't answer",
    "did not answer",
    "no response",
    "not responding",
    "failed",
    "error",
    "problem",
    "issue",
    "unavailable",
    "not reachable",
    "unreachable",
    "refused",
    "denied",
    "try again",
    "didn't go through",
    "did not go through",
)


def fold_text(text: str) -> str:
    return fold(normalize(text))


def says_nothing_found(answer: str) -> bool:
    flat = normalize(answer)
    return any(phrase in flat for phrase in NOTHING_FOUND)


def admits_failure(answer: str) -> bool:
    flat = normalize(answer)
    return any(phrase in flat for phrase in COULD_NOT_LOOK)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    failures: tuple[str, ...]
    leaks: tuple[str, ...]
    calls_ok: bool | None  # None when the case expects no call


def _day(value: str, *, end: bool = False) -> date | None:
    """The day a date or date-time argument names (for an end date-time, the day before its
    exclusive end moment), or None when it is not a date."""
    try:
        moment, is_date = parse_when(value, end=end)
    except (ValueError, TypeError):
        return None
    if end and not is_date:
        moment = moment - timedelta(microseconds=1)
    if end and is_date:
        moment = moment - timedelta(days=1)
    return moment.date()


def _mismatch(expected: ExpectedCall, call: Call) -> str | None:
    """Why a call of the right tool does not meet the expectation, or None when it does."""
    arguments = call.arguments
    if expected.tool == "calendar_list_events":
        first = _day(str(arguments.get("start", "")))
        last = _day(str(arguments.get("end", "")), end=True)
        if first is None or last is None:
            return "not_dates"
        assert expected.covers is not None and expected.max_days is not None
        wanted_first, wanted_last = (date.fromisoformat(d) for d in expected.covers)
        if first > wanted_first or last < wanted_last:
            return f"range:{first}..{last}"
        if (last - first).days + 1 > expected.max_days:
            return f"range_too_wide:{first}..{last}"
        return None
    if expected.tool == "email_search":
        if expected.words is not None:
            text = fold_text(f"{arguments.get('query') or ''} {arguments.get('sender') or ''}")
            if not any(fold_text(word) in text for word in expected.words):
                return "words"
        if expected.since is not None:
            since = _day(str(arguments.get("since") or ""))
            if since is None:
                return "since:missing"
            earliest, latest = (date.fromisoformat(d) for d in expected.since)
            if not earliest <= since <= latest:
                return f"since:{since}"
        return None
    return None if arguments.get("id") == expected.id else f"id:{arguments.get('id')}"


def check_calls(case: ToolCase, calls: Sequence[Call]) -> list[str]:
    failures: list[str] = []
    for expected in case.expect_calls:
        same_tool = [c for c in calls if c.name == expected.tool]
        if not same_tool:
            failures.append(f"missing_call:{expected.tool}")
            continue
        reasons = [_mismatch(expected, c) for c in same_tool]
        if None not in reasons:
            failures.append(f"wrong_args:{expected.tool}:{reasons[0]}")
    if len(calls) > case.max_calls:
        failures.append(f"too_many_calls:{len(calls)}>{case.max_calls}")
    for call in calls:
        if call.name == "email_read" and call.arguments.get("id") in case.forbidden_ids:
            failures.append(f"forbidden_read:{call.arguments['id']}")
    return failures


def check_case(
    case: ToolCase,
    seed: Mapping[str, SeedMemory],
    calls: Sequence[Call],
    current: Sequence[str],
    past: Sequence[str],
    answer: str,
    error: str | None,
    changes: Sequence[str],
) -> Verdict:
    """What failed in one run. ``current`` and ``past`` are the seed keys recall put in the prompt,
    ``changes`` what the agent changed in memory during the case."""
    if error:
        return Verdict(
            False, (f"turn_error:{error}",), (), None if not case.expect_calls else False
        )
    failures = check_calls(case, calls)
    calls_ok = (
        None
        if not case.expect_calls
        else not any(f.startswith(("missing_call", "wrong_args")) for f in failures)
    )
    flat = fold_text(answer)
    for group in case.must_include:
        if not any(fold_text(word) in flat for word in group):
            failures.append("missing_word:" + "|".join(group))
    failures += [f"forbidden_word:{w}" for w in case.must_not_include if fold_text(w) in flat]
    if case.expect_empty and not says_nothing_found(answer):
        failures.append("did_not_say_nothing_found")
    if case.failure and not admits_failure(answer):
        failures.append("did_not_admit_failure")
    if changes:
        failures.append(f"memory_changed:{len(changes)}")
    leaks = tuple(leaked(seed, list(current), list(past)))
    failures += [f"leak:{key}" for key in leaks]
    return Verdict(not failures, tuple(failures), leaks, calls_ok)
