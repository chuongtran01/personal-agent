import json
from collections import Counter

import pytest
from memory_cases import InvalidDataset
from memory_retrieval_cases import DATASETS, SPLITS, load_all, load_cases

ROW = {
    "id": "m900",
    "category": "changed_fact",
    "question": "Where do I live?",
    "relevant_keys": ["home-city"],
    "forbidden_keys": ["home-city-old"],
    "include_external": False,
    "include_upcoming": False,
    "expect_no_answer": False,
    "trap_id": None,
    "note": None,
}


def write(directory, rows, split="dev"):
    (directory / f"memory_retrieval.{split}.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )


def load(tmp_path, *rows):
    write(tmp_path, rows)
    return load_cases("dev", directory=tmp_path)


def test_the_committed_splits_load_and_do_not_overlap():
    splits = load_all()

    assert set(splits) == set(SPLITS)
    assert len(splits["dev"]) > len(splits["test"]) > 0


def test_every_category_is_in_both_splits_that_can_hold_it():
    splits = load_all()
    dev = Counter(c.category for c in splits["dev"])
    test = Counter(c.category for c in splits["test"])

    assert set(test) == set(dev)


def test_the_dataset_covers_the_traps_that_search_can_fail():
    traps = {c.trap_id for cases in load_all().values() for c in cases if c.trap_id}

    assert {
        "changed-fact",
        "conflicting-statements",
        "forget-request",
        "stale-event",
        "injection-from-email",
        "near-duplicate",
    } <= traps


def test_every_committed_row_has_exactly_the_same_fields():
    shapes = set()
    for split in SPLITS:
        path = DATASETS / f"memory_retrieval.{split}.jsonl"
        shapes |= {tuple(json.loads(line)) for line in path.read_text().splitlines()}

    assert shapes == {tuple(ROW)}


def test_a_row_missing_a_field_is_refused(tmp_path):
    row = {k: v for k, v in ROW.items() if k != "note"}

    with pytest.raises(InvalidDataset, match="line 1"):
        load(tmp_path, row)


def test_a_valid_row_loads(tmp_path):
    [case] = load(tmp_path, ROW)

    assert case.relevant_keys == ["home-city"]
    assert not case.include_external


def test_an_unknown_split_is_refused(tmp_path):
    with pytest.raises(InvalidDataset, match="unknown split"):
        load_cases("holdout", directory=tmp_path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"surprise": 1}, "line 1"),
        ({"relevant_keys": ["no-such-memory"]}, "unknown memory"),
        ({"forbidden_keys": ["no-such-memory"]}, "unknown memory"),
        ({"forbidden_keys": ["home-city"]}, "both relevant and forbidden"),
        ({"relevant_keys": []}, "exactly when expect_no_answer"),
        ({"expect_no_answer": True}, "exactly when expect_no_answer"),
        ({"relevant_keys": ["home-city-old"], "forbidden_keys": []}, "is superseded"),
        ({"relevant_keys": ["surgery"], "forbidden_keys": []}, "is deleted"),
        ({"relevant_keys": ["dentist-past"], "forbidden_keys": []}, "already ended"),
        ({"relevant_keys": ["ext-design-review"], "forbidden_keys": []}, "is external"),
        ({"relevant_keys": ["new-studio"], "forbidden_keys": []}, "has not started"),
        ({"trap_id": "no-such-trap"}, "unknown trap"),
    ],
)
def test_a_row_the_search_could_never_satisfy_or_that_is_malformed_is_refused(
    tmp_path, change, message
):
    with pytest.raises(InvalidDataset, match=message):
        load(tmp_path, ROW | change)


def test_a_flag_makes_an_external_or_upcoming_memory_a_valid_answer(tmp_path):
    external = ROW | {
        "id": "m901",
        "relevant_keys": ["ext-design-review"],
        "forbidden_keys": [],
        "include_external": True,
    }
    upcoming = ROW | {
        "id": "m902",
        "question": "Next year?",
        "relevant_keys": ["new-studio"],
        "forbidden_keys": [],
        "include_upcoming": True,
    }

    assert len(load(tmp_path, external, upcoming)) == 2


def test_duplicate_ids_are_refused(tmp_path):
    with pytest.raises(InvalidDataset, match="duplicate case ids"):
        load(tmp_path, ROW, ROW)


def test_the_same_question_with_a_different_flag_is_a_different_ask(tmp_path):
    allowed = ROW | {
        "id": "m901",
        "relevant_keys": ["ext-design-review"],
        "forbidden_keys": [],
        "include_external": True,
    }
    write(tmp_path, [ROW])
    write(tmp_path, [allowed], split="test")

    # Same words, different flags: fine. The committed data relies on this.
    assert len(load_cases("test", directory=tmp_path)) == 1
