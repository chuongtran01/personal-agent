import json

import pytest
from memory_cases import InvalidDataset, MemoryCase, load_cases, load_traps

CASE = {
    "id": "m001",
    "category": "update",
    "sessions": [
        {"turns": ["I live in Lisbon."]},
        {"turns": ["I moved to Porto."], "gap_days": 14},
    ],
    "probe": "Where do I live?",
    "expected_behavior": "answer",
    "must_include": ["Porto"],
    "must_not_include": ["Lisbon"],
    "trap_id": "changed-fact",
}


def write(directory, rows, split="dev"):
    (directory / f"memory.{split}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_the_traps_file_holds_the_eight_traps():
    traps = load_traps()

    assert {trap.id for trap in traps} == {
        "changed-fact",
        "conflicting-statements",
        "forget-request",
        "irrelevant-memory",
        "injection-from-email",
        "stale-event",
        "sensitive-content",
        "near-duplicate",
    }


def test_every_trap_checks_something():
    for trap in load_traps():
        assert trap.must_include or trap.must_not_include or trap.expected_behavior == "not_know"


def test_the_committed_splits_load_and_the_dev_split_is_the_larger():
    dev, test = load_cases("dev"), load_cases("test")

    assert len(dev) > len(test) > 0


def test_every_category_is_in_both_splits_and_every_trap_has_dev_cases():
    from memory_cases import Category

    categories = set(Category.__args__)
    dev, test = load_cases("dev"), load_cases("test")

    assert {c.category for c in dev} == categories == {c.category for c in test}
    assert {c.trap_id for c in dev if c.trap_id} == {t.id for t in load_traps()}


def test_no_case_id_or_input_appears_in_both_splits():
    dev, test = load_cases("dev"), load_cases("test")

    def inputs(cases):
        return {(tuple(tuple(s.turns) for s in c.sessions), c.probe) for c in cases}

    assert not {c.id for c in dev} & {c.id for c in test}
    assert not inputs(dev) & inputs(test)


def test_every_case_that_reads_outside_text_says_so_in_its_first_turn():
    for case in load_cases("dev") + load_cases("test"):
        for session in case.sessions:
            if session.external:
                assert any(
                    word in " ".join(session.turns).lower() for word in ("email", "calendar")
                )


def test_every_secret_and_injection_case_checks_the_table_not_only_the_reply():
    for case in load_cases("dev") + load_cases("test"):
        if case.category in ("sensitive", "injection"):
            assert case.must_not_store, case.id


def test_a_valid_case_loads(tmp_path):
    write(tmp_path, [CASE])

    [case] = load_cases("dev", directory=tmp_path)

    assert isinstance(case, MemoryCase)
    assert case.sessions[1].gap_days == 14


def test_an_unknown_split_is_refused(tmp_path):
    with pytest.raises(InvalidDataset, match="unknown split"):
        load_cases("holdout", directory=tmp_path)


def test_a_case_that_checks_nothing_is_refused(tmp_path):
    write(tmp_path, [CASE | {"must_include": [], "must_not_include": []}])

    with pytest.raises(InvalidDataset, match="checks nothing"):
        load_cases("dev", directory=tmp_path)


def test_an_unknown_field_is_refused(tmp_path):
    write(tmp_path, [CASE | {"extra": 1}])

    with pytest.raises(InvalidDataset, match="line 1"):
        load_cases("dev", directory=tmp_path)


def test_duplicate_case_ids_are_refused(tmp_path):
    write(tmp_path, [CASE, CASE])

    with pytest.raises(InvalidDataset, match="duplicate case ids"):
        load_cases("dev", directory=tmp_path)


def test_a_case_naming_an_unknown_trap_is_refused(tmp_path):
    write(tmp_path, [CASE | {"trap_id": "no-such-trap"}])

    with pytest.raises(InvalidDataset, match="unknown trap"):
        load_cases("dev", directory=tmp_path)


def test_a_case_needs_at_least_one_session(tmp_path):
    write(tmp_path, [CASE | {"sessions": []}])

    with pytest.raises(InvalidDataset):
        load_cases("dev", directory=tmp_path)


# The shape of a case ----------------------------------------------------------------------------


def broken(tmp_path, message, **change):
    write(tmp_path, [CASE | change])
    with pytest.raises(InvalidDataset, match=message):
        load_cases("dev", directory=tmp_path)


def test_the_first_session_has_no_previous_session(tmp_path):
    first_late = [{"turns": ["I live in Lisbon."], "gap_days": 3}, {"turns": ["x"], "gap_days": 1}]

    broken(tmp_path, "first session", sessions=first_late)


def test_a_case_that_only_forbids_a_stored_word_checks_something(tmp_path):
    only_a_store_check = {
        "expected_behavior": "ignore",
        "must_include": [],
        "must_not_include": [],
        "must_not_store": ["4111"],
    }
    write(tmp_path, [CASE | only_a_store_check])

    [case] = load_cases("dev", directory=tmp_path)

    assert case.must_not_store == ["4111"]


def test_a_not_know_case_has_no_facts_to_include(tmp_path):
    broken(tmp_path, "does not know", expected_behavior="not_know")


def test_a_word_cannot_be_both_required_and_forbidden_in_the_reply(tmp_path):
    broken(
        tmp_path, "both required and forbidden", must_include=["Porto"], must_not_include=["porto"]
    )


def test_a_word_required_in_the_reply_may_be_forbidden_in_the_table(tmp_path):
    write(tmp_path, [CASE | {"must_not_store": ["Porto"]}])

    assert len(load_cases("dev", directory=tmp_path)) == 1


@pytest.mark.parametrize("field", ["must_include", "must_not_include", "must_not_store"])
def test_an_empty_word_is_refused(tmp_path, field):
    broken(tmp_path, "empty", **{field: [" "]})


def test_an_empty_turn_or_probe_is_refused(tmp_path):
    broken(tmp_path, "turn is empty", sessions=[{"turns": [" "]}])
    broken(tmp_path, "probe is empty", probe=" ")


def test_the_probe_gap_cannot_be_negative_and_defaults_to_zero(tmp_path):
    broken(tmp_path, "probe_gap_days", probe_gap_days=-1)
    write(tmp_path, [CASE])

    assert load_cases("dev", directory=tmp_path)[0].probe_gap_days == 0


# Placing the sessions on the clock ---------------------------------------------------------------


def sessions(*gaps):
    from memory_cases import Session

    return [Session(turns=["x"], gap_days=gap) for gap in gaps]


def test_one_session_comes_the_probe_gap_before_the_probe():
    from datetime import timedelta

    from memory_cases import EVAL_NOW, session_times

    assert session_times(sessions(0), 60) == [EVAL_NOW - timedelta(days=60)]
    assert session_times(sessions(0), 0) == [EVAL_NOW]


def test_each_session_comes_its_gap_after_the_one_before():
    from datetime import timedelta

    from memory_cases import EVAL_NOW, session_times

    times = session_times(sessions(0, 3, 10), 1)

    assert times == [
        EVAL_NOW - timedelta(days=14),
        EVAL_NOW - timedelta(days=11),
        EVAL_NOW - timedelta(days=1),
    ]


def test_the_probe_time_can_be_moved():
    from datetime import UTC, datetime

    from memory_cases import session_times

    when = datetime(2027, 1, 10, 9, 0, tzinfo=UTC)

    assert session_times(sessions(0, 5), 2, probe_at=when)[-1] == datetime(
        2027, 1, 8, 9, 0, tzinfo=UTC
    )


def test_the_stale_event_trap_is_asked_sixty_days_after_its_session():
    trap = next(t for t in load_traps() if t.id == "stale-event")

    assert trap.probe_gap_days == 60 and trap.sessions[0].gap_days == 0


def test_the_secret_and_injection_traps_check_the_table():
    by_id = {t.id: t for t in load_traps()}

    assert (
        by_id["sensitive-content"].must_not_store and by_id["injection-from-email"].must_not_store
    )


def test_an_answer_case_must_name_a_word_the_reply_contains(tmp_path):
    # Otherwise a reply that knew nothing could pass it, and it could not tell memory from none
    broken(tmp_path, "must name a word", must_include=[], must_not_include=["Lisbon"])


def test_an_ignore_case_needs_no_required_word(tmp_path):
    write(tmp_path, [CASE | {"expected_behavior": "ignore", "must_include": []}])

    assert len(load_cases("dev", directory=tmp_path)) == 1


def test_every_committed_answer_case_names_what_the_reply_must_contain():
    for case in load_cases("dev") + load_cases("test"):
        if case.expected_behavior == "answer":
            assert case.must_include, case.id
