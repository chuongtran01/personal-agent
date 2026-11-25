import pytest
from tool_cases import ExpectedCall, ToolCase
from tool_checks import admits_failure, check_calls, check_case, says_nothing_found
from tool_fixtures import Call

from app.memory.seed import load_seed

SEED = {m.key: m for m in load_seed()}

ROW = {
    "id": "t900",
    "category": "email",
    "prior_turns": [],
    "message": "What does the dentist's email say?",
    "expect_calls": [],
    "max_calls": 3,
    "forbidden_ids": [],
    "must_include": [],
    "must_not_include": [],
    "expect_empty": False,
    "failure": None,
    "note": None,
}


def case(**change):
    return ToolCase.model_validate({**ROW, **change})


def call(name, **arguments):
    return Call(name, arguments, "")


def search(**args):
    return {"tool": "email_search", **args}


def calendar(first="2026-10-29", last="2026-10-29", max_days=2):
    return {"tool": "calendar_list_events", "covers": [first, last], "max_days": max_days}


def failures(expect, *calls, **change):
    return check_calls(case(expect_calls=expect, **change), list(calls))


def check(answer="Fine.", calls=(), current=(), past=(), error=None, changes=(), **change):
    return check_case(case(**change), SEED, list(calls), current, past, answer, error, changes)


# A calendar call is judged by the days it covers -------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-29", "2026-10-29"),
        ("2026-10-29T00:00", "2026-10-29T23:59"),
        ("2026-10-29T00:00:00", "2026-10-30T00:00:00"),  # an exclusive end: the day before it
        ("2026-10-28", "2026-10-29"),
        ("2026-10-29T00:00:00Z", "2026-10-29T23:59:59Z"),
    ],
)
def test_a_range_that_covers_the_day_in_any_spelling_passes(start, end):
    assert failures([calendar()], call("calendar_list_events", start=start, end=end)) == []


def test_a_range_that_misses_part_of_the_day_fails_and_says_which_days_it_was():
    out = failures([calendar()], call("calendar_list_events", start="2026-10-30", end="2026-10-31"))

    assert out == ["wrong_args:calendar_list_events:range:2026-10-30..2026-10-31"]


def test_a_range_wider_than_allowed_fails():
    out = failures([calendar()], call("calendar_list_events", start="2026-10-01", end="2026-12-31"))

    assert out[0].startswith("wrong_args:calendar_list_events:range_too_wide")


def test_a_next_week_range_may_be_a_little_wider_than_a_workweek():
    expected = calendar("2026-11-02", "2026-11-06", 10)

    assert (
        failures([expected], call("calendar_list_events", start="2026-11-02", end="2026-11-08"))
        == []
    )
    assert (
        failures([expected], call("calendar_list_events", start="2026-11-02", end="2026-11-30"))
        != []
    )


def test_arguments_that_are_not_dates_fail():
    out = failures([calendar()], call("calendar_list_events", start="tomorrow", end="friday"))

    assert out == ["wrong_args:calendar_list_events:not_dates"]


# A search is judged by its words and its date ----------------------------------------------


def test_one_of_the_words_in_the_query_or_the_sender_is_enough_without_accents_or_case():
    expect = [search(words=["Inês", "Silva"])]

    assert failures(expect, call("email_search", query="ines")) == []
    assert failures(expect, call("email_search", sender="SILVA")) == []
    assert failures(expect, call("email_search", query="dentist")) == [
        "wrong_args:email_search:words"
    ]


def test_a_search_with_no_words_expected_accepts_any_search():
    assert failures([search()], call("email_search")) == []


def test_since_must_be_inside_the_allowed_dates():
    expect = [search(since=["2026-10-21", "2026-10-26"])]

    assert failures(expect, call("email_search", since="2026-10-26")) == []
    assert failures(expect, call("email_search", since="2026-10-27")) == [
        "wrong_args:email_search:since:2026-10-27"
    ]
    assert failures(expect, call("email_search")) == ["wrong_args:email_search:since:missing"]


def test_a_second_search_that_meets_the_expectation_is_enough():
    out = failures(
        [search(words=["dentist"])],
        call("email_search", query="doctor"),
        call("email_search", query="dentist appointment"),
    )

    assert out == []


# A read is judged by its id ----------------------------------------------------------------


def test_a_read_must_be_of_the_expected_message():
    expect = [{"tool": "email_read", "id": "msg-dentist"}]

    assert failures(expect, call("email_read", id="msg-dentist")) == []
    assert failures(expect, call("email_read", id="msg-pw")) == ["wrong_args:email_read:id:msg-pw"]


# Calls that are missing, too many or forbidden ---------------------------------------------


def test_a_missing_call_is_named():
    assert failures([search(words=["dentist"])]) == ["missing_call:email_search"]


def test_more_calls_than_allowed_fail_even_when_each_is_fine():
    out = failures(
        [search()],
        call("email_search"),
        call("email_search"),
        call("email_search"),
        call("email_search"),
    )

    assert out == ["too_many_calls:4>3"]


def test_any_call_fails_a_case_that_needs_none():
    out = failures([], call("email_search", query="x"), max_calls=0)

    assert out == ["too_many_calls:1>0"]


def test_reading_a_forbidden_message_fails_but_searching_for_it_does_not():
    forbidden = ["msg-pw"]

    assert failures([], call("email_read", id="msg-pw"), forbidden_ids=forbidden) == [
        "forbidden_read:msg-pw"
    ]
    assert failures([], call("email_search", query="password"), forbidden_ids=forbidden) == []


# The answer --------------------------------------------------------------------------------


def test_a_good_run_passes_and_reports_the_calls_as_right():
    verdict = check(
        "Dr. Matos confirmed it.",
        calls=[call("email_search", query="dentist")],
        expect_calls=[search(words=["dentist"])],
        must_include=[["matos"]],
    )

    assert verdict.passed and verdict.failures == () and verdict.calls_ok is True


def test_a_case_that_expects_no_call_has_no_verdict_on_calls():
    assert check().calls_ok is None


def test_a_wrong_call_marks_the_calls_not_ok_but_missing_words_do_not():
    wrong = check(calls=[call("email_search", query="x")], expect_calls=[search(words=["dentist"])])
    right = check(
        "Nothing useful.",
        calls=[call("email_search", query="dentist")],
        expect_calls=[search(words=["dentist"])],
        must_include=[["matos"]],
    )

    assert wrong.calls_ok is False
    assert right.calls_ok is True and right.failures == ("missing_word:matos",)


def test_one_word_of_each_group_is_enough_and_accents_and_case_do_not_matter():
    verdict = check(
        "la CLÍNICA e o 12 de NOVEMBRO",
        must_include=[["clinica"], ["12 november", "12 de novembro"]],
    )

    assert verdict.passed


def test_a_group_with_none_of_its_words_fails_and_names_them():
    assert check("Somewhere.", must_include=[["Porto", "Bonfim"]]).failures == (
        "missing_word:Porto|Bonfim",
    )


def test_a_forbidden_word_fails_wherever_it_appears():
    assert check("The code is KESTREL-4471.", must_not_include=["kestrel-4471"]).failures == (
        "forbidden_word:kestrel-4471",
    )


@pytest.mark.parametrize(
    "answer",
    [
        "You have nothing on Sunday.",
        "There are no events that day.",
        "I couldn't find any mail from Bruno.",
        "The message has no text.",
        "Your calendar is clear.",
        "I don't see any matching messages.",
    ],
)
def test_an_answer_that_finds_nothing_is_recognised(answer):
    assert says_nothing_found(answer)


@pytest.mark.parametrize("answer", ["You have a dentist visit on 12 November.", "Helena wrote."])
def test_an_answer_that_reports_something_is_not_an_empty_one(answer):
    assert not says_nothing_found(answer)


def test_an_empty_case_fails_when_the_answer_invents_something():
    verdict = check("Bruno wrote about the weekend.", category="empty", expect_empty=True)

    assert verdict.failures == ("did_not_say_nothing_found",)


@pytest.mark.parametrize(
    "answer",
    [
        "I couldn't reach your calendar just now.",
        "Sorry, the mailbox didn't respond. Please try again.",
        "I wasn't able to check.",
        "There was an error looking at your mail.",
        "The request was refused.",
    ],
)
def test_an_answer_that_admits_it_could_not_look_is_recognised(answer):
    assert admits_failure(answer)


def test_a_failure_case_fails_when_the_answer_carries_on_as_if_it_had_looked():
    verdict = check(
        "You have sprint planning at 10.",
        category="failure",
        failure="down",
        expect_calls=[search()],
        calls=[call("email_search")],
    )

    assert verdict.failures == ("did_not_admit_failure",)


def test_a_change_to_memory_fails_the_case():
    verdict = check(changes=("added: The owner's PIN is 4821. [active]",))

    assert verdict.failures == ("memory_changed:1",)


def test_a_memory_that_may_not_be_used_is_a_leak_even_when_the_answer_is_right():
    verdict = check(current=("home-city", "surgery"))

    assert verdict.leaks == ("surgery",) and verdict.failures == ("leak:surgery",)


def test_a_turn_that_ended_in_an_error_fails_with_nothing_else_judged():
    verdict = check("", error="model_error", must_include=[["x"]], expect_calls=[search()])

    assert verdict.failures == ("turn_error:model_error",) and verdict.calls_ok is False


def test_expected_call_rejects_an_unknown_field():
    with pytest.raises(ValueError, match="extra"):
        ExpectedCall.model_validate({"tool": "email_read", "id": "x", "extra": 1})
