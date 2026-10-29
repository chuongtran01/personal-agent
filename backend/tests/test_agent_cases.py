import json
from collections import Counter

import pytest
from agent_cases import DATASETS, SPLITS, load_all, load_cases
from memory_cases import InvalidDataset

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


def write(directory, rows, split="dev"):
    (directory / f"agent.{split}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def load(tmp_path, *rows):
    write(tmp_path, rows)
    return load_cases("dev", directory=tmp_path)


def test_the_committed_splits_load_and_do_not_overlap():
    splits = load_all()

    assert set(splits) == set(SPLITS)
    assert len(splits["dev"]) > len(splits["test"]) > 0


def test_every_category_is_in_both_splits():
    splits = load_all()

    assert {c.category for c in splits["test"]} == {c.category for c in splits["dev"]}


def test_the_dataset_covers_every_trap_a_read_only_agent_can_fail():
    traps = {c.trap_id for cases in load_all().values() for c in cases if c.trap_id}

    assert {
        "changed-fact",
        "conflicting-statements",
        "forget-request",
        "irrelevant-memory",
        "injection-from-email",
        "stale-event",
        "sensitive-content",
        "near-duplicate",
    } == traps


def test_rows_are_present_in_full_and_ids_are_unique_across_splits():
    rows = [
        json.loads(line)
        for split in SPLITS
        for line in (DATASETS / f"agent.{split}.jsonl").read_text().splitlines()
    ]

    assert all(set(row) == set(ROW) for row in rows)
    ids = Counter(row["id"] for row in rows)
    assert max(ids.values()) == 1


def test_a_valid_row_loads(tmp_path):
    (case,) = load(tmp_path, ROW)

    assert case.must_include == [["Porto"]]
    assert case.expect_unknown is False


def test_a_missing_field_is_rejected(tmp_path):
    row = {key: value for key, value in ROW.items() if key != "must_include"}

    with pytest.raises(InvalidDataset, match="line 1"):
        load(tmp_path, row)


def test_an_extra_field_is_rejected(tmp_path):
    with pytest.raises(InvalidDataset, match="line 1"):
        load(tmp_path, {**ROW, "surprise": 1})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"must_recall": ["nope"]}, "unknown memory nope"),
        ({"must_not_recall": ["home-city"]}, "both recalled and not recalled"),
        ({"must_recall": ["home-city-old"], "must_not_recall": []}, "is superseded"),
        ({"must_recall": ["ext-injection"], "must_not_recall": []}, "is external"),
        ({"must_recall": ["new-studio"], "must_not_recall": []}, "has not started"),
        ({"must_not_recall": ["diet"]}, "one the agent may recall"),
        ({"must_include": [[]]}, "empty group"),
        ({"must_include": [["Porto", " "]]}, "empty group or word"),
        ({"must_not_include": [""]}, "empty word"),
        ({"must_not_include": ["porto"]}, "both required and forbidden"),
        ({"message": "  "}, "a message is empty"),
        ({"prior_turns": [""]}, "a message is empty"),
        ({"expect_unknown": True}, "has no facts"),
        ({"category": "follow_up"}, "needs earlier turns"),
        ({"trap_id": "no-such-trap"}, "unknown trap"),
        (
            {"category": "history", "must_recall": ["home-city"], "must_not_recall": []},
            "must recall a replaced or ended memory",
        ),
        (
            {
                "category": "history",
                "must_recall": ["home-city-old"],
                "must_not_recall": ["dentist-past"],
            },
            "one a history question may recall",
        ),
    ],
)
def test_a_bad_row_is_rejected(tmp_path, change, message):
    with pytest.raises(InvalidDataset, match=message):
        load(tmp_path, {**ROW, **change})


def test_a_history_case_may_recall_replaced_and_ended_memories(tmp_path):
    rows = [
        {
            **ROW,
            "category": "history",
            "must_recall": ["home-city", "home-city-old", "dentist-past"],
            "must_not_recall": ["surgery", "ext-injection", "new-studio"],
        }
    ]

    (case,) = load(tmp_path, *rows)

    assert case.category == "history"


def test_the_history_cases_cover_replaced_facts_and_ended_events():
    history = [c for cases in load_all().values() for c in cases if c.category == "history"]
    recalled = {key for case in history for key in case.must_recall}

    assert {"home-city-old", "dentist-past", "faro-flight"} <= recalled


def test_a_row_that_checks_nothing_is_rejected(tmp_path):
    row = {
        **ROW,
        "must_recall": [],
        "must_not_recall": [],
        "must_include": [],
        "must_not_include": [],
    }

    with pytest.raises(InvalidDataset, match="checks nothing"):
        load(tmp_path, row)


def test_duplicate_ids_are_rejected(tmp_path):
    with pytest.raises(InvalidDataset, match="duplicate case ids"):
        load(tmp_path, ROW, {**ROW, "message": "Another question?"})


def test_the_same_message_in_both_splits_is_rejected(monkeypatch):
    from agent_cases import AgentCase

    case = AgentCase.model_validate(ROW)
    other = case.model_copy(update={"id": "a901"})
    monkeypatch.setattr(
        "agent_cases.load_cases", lambda split: [case] if split == "dev" else [other]
    )

    with pytest.raises(InvalidDataset, match="message appears more than once"):
        load_all()


def test_an_unknown_split_is_rejected():
    with pytest.raises(InvalidDataset, match="unknown split"):
        load_cases("holdout")


def test_an_empty_file_is_an_empty_split(tmp_path):
    (tmp_path / "agent.dev.jsonl").write_text("")

    assert load_cases("dev", directory=tmp_path) == []
