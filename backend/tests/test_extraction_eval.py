import asyncio
import json
from datetime import datetime

import extraction_eval
from extraction_cases import EVAL_TZ, load_cases
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


# The extractor as a writer ---------------------------------------------------------------------


async def test_the_extract_writer_stores_every_proposed_add_and_nothing_else():
    from datetime import UTC

    from extraction_eval import ExtractOnly
    from scripted_llm import ScriptedStructured, answer

    proposals = {
        "proposals": [
            {"op": "add", "kind": "event", "content": "Dentist.", "valid_until": "2027-03-03"},
            {"op": "delete", "kind": None, "content": "The surgery.", "valid_until": None},
        ]
    }
    llm = ScriptedStructured(answer(proposals))

    outcome = await ExtractOnly(llm).run(case("e006"), EVAL_NOW)

    assert outcome.changes == [
        Add("event", "Dentist.", None, datetime(2027, 3, 4, 0, 0, tzinfo=UTC))
    ]
    assert (outcome.input_tokens, outcome.output_tokens) == (20, 8)


async def test_the_extract_writer_gives_the_extractor_the_whole_case():
    from extraction_eval import ExtractOnly
    from scripted_llm import ScriptedStructured, answer

    llm = ScriptedStructured(answer({"proposals": []}))

    await ExtractOnly(llm).run(case("e005"), EVAL_NOW)

    prompt = llm.calls[0].prompt
    assert "owner: I'm thinking about getting a dog." in prompt
    assert "assistant: Nice! Do you have a breed in mind?" in prompt
    assert "1. A beagle, and I'll call her Luna." in prompt
    assert "Europe/Lisbon" in llm.calls[0].system


async def test_the_extract_writer_passes_the_assistants_reply_as_context():
    from extraction_eval import ExtractOnly
    from scripted_llm import ScriptedStructured, answer

    llm = ScriptedStructured(answer({"proposals": []}))

    await ExtractOnly(llm).run(case("e016"), EVAL_NOW)

    assert (
        "<assistant_reply>\nYour sister is called Ana.\n</assistant_reply>" in llm.calls[0].prompt
    )


async def test_a_failed_extraction_fails_only_its_case():
    from extraction_eval import ExtractOnly
    from scripted_llm import ScriptedStructured

    from app.agent.llm import LLMError

    writer = ExtractOnly(ScriptedStructured(LLMError("down", retryable=True)))

    result = await run_case(writer, case("e010"), 1)

    assert not result.passed and result.failures == ("error:LLMError",)


async def test_main_runs_cases_at_the_same_time_up_to_the_limit(tmp_path):
    running = peak = 0

    class Slow:
        async def run(self, case, now):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return Outcome()

    extraction_eval.WRITERS["slow"] = Slow
    try:
        await extraction_eval.main("dev", "slow", 1, [], tmp_path, concurrency=3)
    finally:
        del extraction_eval.WRITERS["slow"]

    assert peak == 3


async def test_a_run_records_what_the_writer_proposed_and_the_report_prints_it(tmp_path, capsys):
    from extraction_checks import Supersede

    class Writes:
        async def run(self, case, now):
            return Outcome(
                [
                    Add("event", "Dentist.", None, datetime(2027, 3, 4, tzinfo=EVAL_TZ)),
                    Supersede("city", "fact", "Porto."),
                    Delete("surgery"),
                ]
            )

    result = await run_case(Writes(), case("e010"), 1)

    assert result.proposed == (
        "add [event] Dentist. (until 2027-03-04T00:00:00+00:00)",
        "replace city: [fact] Porto.",
        "delete surgery",
    )
    extraction_eval.print_failures([result])
    assert "wrote: replace city: [fact] Porto." in capsys.readouterr().out
