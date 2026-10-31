import json
from collections import Counter

import pytest
from extraction_cases import DATASETS, SPLITS, ExtractionCase, load_all, load_cases
from memory_cases import InvalidDataset

ROW = {
    "id": "e900",
    "category": "supersede",
    "existing": [
        {"key": "city", "kind": "fact", "content": "The owner lives in Lisbon.", "status": "active"}
    ],
    "context": [],
    "owner": ["I moved to Porto."],
    "reply": None,
    "writes": [
        {"kind": None, "include": [["Porto"]], "replaces": "city", "starts": None, "ends": None}
    ],
    "deletes": [],
    "no_changes": False,
    "must_not_store": [],
    "note": None,
}


def load(tmp_path, *rows):
    (tmp_path / "extraction.dev.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return load_cases("dev", directory=tmp_path)


def broken(tmp_path, message, **change):
    with pytest.raises(InvalidDataset, match=message):
        load(tmp_path, {**ROW, **change})


def test_the_committed_splits_load_and_are_not_empty():
    splits = load_all()

    assert set(splits) == set(SPLITS)
    assert len(splits["dev"]) > len(splits["test"]) > 0


def test_every_category_is_in_both_splits():
    splits = load_all()
    dev, test = (Counter(c.category for c in splits[s]) for s in ("dev", "test"))

    assert set(dev) == set(test) == set(ExtractionCase.model_fields["category"].annotation.__args__)


def test_every_trap_has_an_extraction_case():
    notes = " ".join((c.note or "").lower() for cases in load_all().values() for c in cases)

    for trap in (
        "changed-fact",
        "conflicting-statements",
        "forget-request",
        "injection",
        "sensitive-content",
        "near-duplicate",
        "stale-event",
    ):
        assert trap in notes or trap.replace("-", " ") in notes, trap


def test_a_valid_row_loads(tmp_path):
    assert load(tmp_path, ROW)[0].id == "e900"


def test_an_empty_file_is_an_empty_split(tmp_path):
    (tmp_path / "extraction.dev.jsonl").write_text("")

    assert load_cases("dev", directory=tmp_path) == []


def test_an_unknown_split_or_missing_file_is_refused(tmp_path):
    with pytest.raises(InvalidDataset, match="unknown split"):
        load_cases("nope", directory=tmp_path)
    with pytest.raises(InvalidDataset):
        load_cases("dev", directory=tmp_path)


def test_a_malformed_row_names_its_line(tmp_path):
    (tmp_path / "extraction.dev.jsonl").write_text(json.dumps(ROW) + "\n{not json\n")

    with pytest.raises(InvalidDataset, match="line 2"):
        load_cases("dev", directory=tmp_path)


def test_a_missing_field_or_an_extra_one_is_refused(tmp_path):
    with pytest.raises(InvalidDataset):
        load(tmp_path, {k: v for k, v in ROW.items() if k != "reply"})
    with pytest.raises(InvalidDataset):
        load(tmp_path, {**ROW, "surprise": 1})


def test_duplicate_ids_are_refused(tmp_path):
    with pytest.raises(InvalidDataset, match="duplicate case ids"):
        load(tmp_path, ROW, ROW)


def test_a_change_to_an_unknown_memory_is_refused(tmp_path):
    write = {**ROW["writes"][0], "replaces": "nope"}
    broken(tmp_path, "unknown memory nope", writes=[write])
    broken(tmp_path, "unknown memory nope", writes=[], deletes=["nope"], category="delete")


def test_a_change_to_a_memory_that_is_not_active_is_refused(tmp_path):
    old = {
        "key": "city",
        "kind": "fact",
        "content": "The owner lived in Lisbon.",
        "status": "superseded",
    }
    broken(tmp_path, "not active", existing=[old])


def test_a_memory_cannot_be_replaced_and_deleted_or_changed_twice(tmp_path):
    broken(tmp_path, "both replaced and deleted", deletes=["city"])
    broken(tmp_path, "same memory", writes=[ROW["writes"][0], ROW["writes"][0]])


def test_a_case_must_check_something(tmp_path):
    broken(tmp_path, "checks nothing", category="no_write", writes=[], no_changes=False)


def test_no_changes_cannot_also_ask_for_changes(tmp_path):
    broken(tmp_path, "no_changes with changes", no_changes=True)


def test_a_word_cannot_be_both_required_and_forbidden(tmp_path):
    broken(tmp_path, "both required and forbidden", must_not_store=["porto"])


def test_a_write_needs_words_and_no_empty_ones(tmp_path):
    for include in ([], [[]], [[" "]]):
        broken(tmp_path, "a write", writes=[{**ROW["writes"][0], "include": include}])


def test_empty_messages_are_refused(tmp_path):
    broken(tmp_path, "owner message is empty", owner=[" "])
    broken(tmp_path, "context message is empty", context=[{"role": "owner", "text": ""}])
    broken(tmp_path, "reply is empty", reply=" ")


def test_an_event_cannot_end_before_it_starts(tmp_path):
    write = {**ROW["writes"][0], "starts": "2027-01-02", "ends": "2027-01-01"}
    broken(tmp_path, "ends before it starts", writes=[write])


def test_each_category_has_its_own_shape(tmp_path):
    broken(tmp_path, "supersede case must replace", writes=[{**ROW["writes"][0], "replaces": None}])
    broken(tmp_path, "delete case must delete", category="delete")
    broken(tmp_path, "must give a start date", category="event_date")
    broken(tmp_path, "at least two writes", category="multi_fact")
    broken(tmp_path, "changes nothing", category="delete_unclear")
    broken(tmp_path, "must not be stored", category="sensitive")
    no_reply = {
        **ROW,
        "category": "assistant_claim",
        "writes": [],
        "no_changes": True,
        "must_not_store": ["x"],
    }
    with pytest.raises(InvalidDataset, match="needs a reply"):
        load(tmp_path, no_reply)


def test_the_same_input_in_both_splits_is_refused(tmp_path):
    for split in SPLITS:
        (tmp_path / f"extraction.{split}.jsonl").write_text(
            json.dumps({**ROW, "id": f"e9{split}"}) + "\n"
        )

    with pytest.raises(InvalidDataset, match="more than once"):
        load_all(tmp_path)


def test_the_same_id_in_both_splits_is_refused(tmp_path):
    for split, owner in (("dev", "I moved to Porto."), ("test", "I moved to Braga.")):
        row = {
            **ROW,
            "owner": [owner],
            "writes": [{**ROW["writes"][0], "include": [[owner[-6:-1]]]}],
        }
        (tmp_path / f"extraction.{split}.jsonl").write_text(json.dumps(row) + "\n")

    with pytest.raises(InvalidDataset, match="id appears more than once"):
        load_all(tmp_path)


def test_the_dataset_directory_is_the_one_in_the_repo():
    assert (DATASETS / "extraction.dev.jsonl").exists()
