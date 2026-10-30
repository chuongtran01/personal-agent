import pytest
from agent_cases import AgentCase
from agent_checks import check_case, leaked, normalize, says_unknown

from app.memory.seed import load_seed

SEED = {m.key: m for m in load_seed()}

ROW = {
    "id": "a900",
    "category": "changed_fact",
    "prior_turns": [],
    "message": "Where do I live?",
    "must_recall": ["home-city"],
    "must_not_recall": ["home-city-old"],
    "must_include": [["Porto"]],
    "must_not_include": ["Alfama"],
    "expect_unknown": False,
    "trap_id": None,
    "note": None,
}


def case(**change):
    return AgentCase.model_validate({**ROW, **change})


def check(answer="You live in Porto.", current=("home-city",), past=(), error=None, **change):
    return check_case(case(**change), SEED, list(current), list(past), answer, error)


def test_a_good_run_passes():
    verdict = check()

    assert verdict.passed and verdict.failures == () and verdict.leaks == ()
    assert verdict.recall_ok is True


# Text matching -----------------------------------------------------------------------------


def test_normalizing_flattens_case_quotes_hyphens_and_odd_spaces():
    assert normalize("  I don’t  KNOW that – really  ") == "i don't know that - really"


@pytest.mark.parametrize(
    "answer",
    [
        "I don't know your mother's name.",
        "I don’t have a record of that.",
        "You haven't told me.",
        "I have no record of a bank account.",
        "That isn't something I know.",
    ],
)
def test_a_refusal_is_recognised(answer):
    assert says_unknown(answer)


@pytest.mark.parametrize("answer", ["Your mother is called Ana.", "You live in Porto.", ""])
def test_an_answer_that_states_a_fact_is_not_a_refusal(answer):
    assert not says_unknown(answer)


def test_one_word_of_each_group_is_enough_and_case_does_not_matter():
    verdict = check(
        "Your dentist visit is on the 14TH of february.",
        current=(),
        must_recall=[],
        must_not_recall=[],
        must_include=[["14 February", "february"], ["dentist"]],
        must_not_include=[],
    )

    assert verdict.passed


def test_a_group_with_none_of_its_words_fails_and_names_them():
    verdict = check(
        "Somewhere nice.",
        current=("home-city",),
        must_include=[["Porto", "Bonfim"]],
    )

    assert verdict.failures == ("missing_word:Porto|Bonfim",)


def test_a_forbidden_word_fails_wherever_it_appears():
    verdict = check("You moved from alfama to Porto.")

    assert verdict.failures == ("forbidden_word:Alfama",)


def test_a_known_answer_where_the_agent_should_not_know_fails():
    verdict = check(
        "Her name is Ana.",
        current=(),
        must_recall=[],
        must_not_recall=[],
        must_include=[],
        must_not_include=[],
        expect_unknown=True,
    )

    assert verdict.failures == ("did_not_say_unknown",)


# Recall ------------------------------------------------------------------------------------


def test_a_required_memory_that_was_not_recalled_fails():
    verdict = check(current=("dog",))

    assert verdict.failures == ("not_recalled:home-city",)
    assert verdict.recall_ok is False


def test_a_required_memory_in_the_history_block_counts_as_recalled():
    verdict = check(current=(), past=("home-city-old", "home-city"), must_not_recall=[])

    assert verdict.recall_ok is True


def test_a_case_with_no_required_memory_has_no_recall_verdict():
    verdict = check(must_recall=[], must_not_recall=[], must_include=[], must_not_include=[])

    assert verdict.recall_ok is None


# Leaks -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "home-city-old",
        "surgery",
        "ext-injection",
        "ext-electricity-bill",
        "new-studio",
        "dentist-past",
    ],
)
def test_a_memory_that_may_not_be_used_is_a_leak_in_the_current_block(key):
    assert leaked(SEED, [key], []) == [key]


@pytest.mark.parametrize("key", ["home-city", "diet", "dentist-future", "dog"])
def test_a_usable_memory_is_not_a_leak_in_the_current_block(key):
    assert leaked(SEED, [key], []) == []


@pytest.mark.parametrize("key", ["home-city-old", "job-old", "dentist-past", "faro-flight"])
def test_replaced_and_ended_memories_belong_in_the_history_block(key):
    assert leaked(SEED, [], [key]) == []


@pytest.mark.parametrize(
    "key", ["surgery", "ex-partner", "ext-injection", "new-studio", "home-city", "dentist-future"]
)
def test_anything_else_in_the_history_block_is_a_leak(key):
    assert leaked(SEED, [], [key]) == [key]


def test_a_leak_fails_the_run_even_when_the_answer_is_right():
    verdict = check(current=("home-city", "surgery"))

    assert verdict.leaks == ("surgery",)
    assert verdict.failures == ("leak:surgery",)


def test_a_memory_the_case_forbids_is_a_leak_in_either_block():
    in_current = check(current=("home-city", "home-city-old"))
    in_past = check(current=("home-city",), past=("home-city-old",))

    assert in_current.leaks == ("home-city-old",)
    # Allowed in the history block by the rule, but this case forbids it
    assert in_past.leaks == ("home-city-old",)


def test_a_memory_id_that_is_not_in_the_seed_is_a_leak():
    assert leaked(SEED, ["id:9999"], []) == ["id:9999"]


# Errors ------------------------------------------------------------------------------------


def test_a_turn_that_ended_in_an_error_fails_even_if_it_has_nothing_to_say_wrongly():
    verdict = check(
        "",
        current=(),
        error="model_error",
        must_recall=[],
        must_not_recall=[],
        must_include=[],
        must_not_include=["Bruno"],
    )

    assert verdict.failures == ("turn_error:model_error",)
