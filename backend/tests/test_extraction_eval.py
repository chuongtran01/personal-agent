import json
from datetime import datetime

import extraction_eval
from extraction_cases import load_cases
from extraction_checks import Add, Delete
from extraction_eval import NoWrites, Outcome, run_case, summarize
from memory_cases import EVAL_NOW


def case(id):
    return next(c for c in load_cases("dev") if c.id == id)


class Scripted:
    """A writer that answers each case with fixed changes."""

    def __init__(self, changes=None, fail=None):
        self.changes, self.fail, self.seen = changes or {}, fail, []

    async def run(self, case, now):
        self.seen.append((case.id, now))
        if self.fail:
            raise self.fail
        return Outcome(self.changes.get(case.id, []), input_tokens=10, output_tokens=2)


async def test_the_baseline_passes_only_the_cases_that_ask_for_no_change():
    cases = load_cases("dev")
    runs = [await run_case(NoWrites(), c, 1) for c in cases]

    assert [r.passed for r in runs] == [c.no_changes for c in cases]
    assert all(not r.stored and not r.wrong_changes for r in runs)


async def test_the_writer_gets_the_fixed_clock():
    writer = Scripted()

    await run_case(writer, case("e010"), 1)

    assert writer.seen == [("e010", EVAL_NOW)]


async def test_a_writer_that_raises_fails_its_case_and_the_run_goes_on():
    result = await run_case(Scripted(fail=RuntimeError("boom")), case("e010"), 1)

    assert not result.passed
    assert result.failures == ("error:RuntimeError",)
    assert result.error == "RuntimeError: boom"


async def test_a_run_records_the_changes_and_tokens():
    result = await run_case(
        Scripted({"e001": [Add("fact", "penicillin allergy")]}), case("e001"), 2
    )

    assert (result.passed, result.changes, result.run) == (True, 1, 2)
    assert (result.input_tokens, result.output_tokens) == (10, 2)


async def test_a_wrong_delete_and_a_stored_word_are_counted_separately():
    runs = [
        await run_case(Scripted({"e029": [Delete("sister")]}), case("e029"), 1),
        await run_case(Scripted({"e032": [Add("fact", "wifi sunflower-42")]}), case("e032"), 1),
    ]

    summary = summarize(runs)

    assert (summary["wrong_changes"], summary["stored"]) == (1, 1)
    assert summary["passed"] == 0


def test_the_summary_counts_by_category():
    from extraction_eval import CaseRun

    def row(category, passed, changes=0, error=None):
        return CaseRun("x", category, 1, passed, (), (), (), changes, error, 5, 1)

    summary = summarize([row("add", True, 1), row("add", False, 3), row("delete", True, error="e")])

    assert summary["by_category"] == {
        "add": {"runs": 2, "passed": 1},
        "delete": {"runs": 1, "passed": 1},
    }
    assert summary["runs"] == 3 and summary["errors"] == 1
    assert summary["changes_per_turn"] == 1.33
    assert (summary["input_tokens"], summary["output_tokens"]) == (15, 3)


def test_an_empty_summary_does_not_divide_by_zero():
    assert summarize([])["changes_per_turn"] == 0


async def test_main_writes_a_results_file_and_passes_the_gate_for_the_baseline(tmp_path, capsys):
    code = await extraction_eval.main("test", "none", 2, [], tmp_path)

    files = list(tmp_path.glob("extraction-test-none-*.json"))
    saved = json.loads(files[0].read_text())
    assert code == 0 and len(files) == 1
    assert saved["repeat"] == 2 and len(saved["runs"]) == 2 * len(load_cases("test"))
    assert datetime.fromisoformat(saved["eval_now"]) == EVAL_NOW
    out = capsys.readouterr().out
    assert "gate: nothing forbidden stored" in out and "failing cases" in out


async def test_main_exits_one_when_a_run_stored_something_forbidden(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(
        extraction_eval.WRITERS,
        "bad",
        lambda: Scripted({"e031": [Add("fact", "card 4111 1111 1111 1111")]}),
    )

    code = await extraction_eval.main("dev", "bad", 1, ["e031"], tmp_path)

    assert code == 1
    assert "GATE FAILED" in capsys.readouterr().out


async def test_main_runs_only_the_cases_asked_for_and_refuses_unknown_ones(tmp_path):
    import pytest
    from memory_cases import InvalidDataset

    await extraction_eval.main("dev", "none", 1, ["e010", "e011"], tmp_path)
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())

    assert [r["id"] for r in saved["runs"]] == ["e010", "e011"]
    with pytest.raises(InvalidDataset, match="e999"):
        await extraction_eval.main("dev", "none", 1, ["e999"], tmp_path)
