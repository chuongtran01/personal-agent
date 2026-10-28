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


def test_the_committed_splits_load_and_are_empty_for_now():
    assert load_cases("dev") == []
    assert load_cases("test") == []


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
