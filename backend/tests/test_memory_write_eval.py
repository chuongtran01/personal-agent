import asyncio
import json
from datetime import datetime

import memory_write_eval
import pytest
from memory_cases import EVAL_NOW
from memory_write_cases import EVAL_TZ, load_cases
from memory_write_checks import Add, Delete, Supersede
from memory_write_eval import NoWrites, Outcome, run_case, summarize
from scripted_llm import answer

from app.agent.llm import Usage


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
    from memory_write_eval import CaseRun

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
    code = await memory_write_eval.main("test", "none", 2, [], tmp_path)

    files = list(tmp_path.glob("memory-write-test-none-*.json"))
    saved = json.loads(files[0].read_text())
    assert code == 0 and len(files) == 1
    assert saved["repeat"] == 2 and len(saved["runs"]) == 2 * len(load_cases("test"))
    assert datetime.fromisoformat(saved["eval_now"]) == EVAL_NOW
    out = capsys.readouterr().out
    assert "gate: nothing forbidden stored" in out and "failing cases" in out


async def test_main_exits_one_when_a_run_stored_something_forbidden(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(
        memory_write_eval.WRITERS,
        "bad",
        lambda: Scripted({"e031": [Add("fact", "card 4111 1111 1111 1111")]}),
    )

    code = await memory_write_eval.main("dev", "bad", 1, ["e031"], tmp_path)

    assert code == 1
    assert "GATE FAILED" in capsys.readouterr().out


async def test_main_runs_only_the_cases_asked_for_and_refuses_unknown_ones(tmp_path):
    import pytest
    from memory_cases import InvalidDataset

    await memory_write_eval.main("dev", "none", 1, ["e010", "e011"], tmp_path)
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())

    assert [r["id"] for r in saved["runs"]] == ["e010", "e011"]
    with pytest.raises(InvalidDataset, match="e999"):
        await memory_write_eval.main("dev", "none", 1, ["e999"], tmp_path)


# The extractor as a writer ---------------------------------------------------------------------


async def test_the_extract_writer_stores_every_proposed_add_and_nothing_else():
    from datetime import UTC

    from memory_write_eval import ExtractOnly
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
    from memory_write_eval import ExtractOnly
    from scripted_llm import ScriptedStructured, answer

    llm = ScriptedStructured(answer({"proposals": []}))

    await ExtractOnly(llm).run(case("e005"), EVAL_NOW)

    prompt = llm.calls[0].prompt
    assert "owner: I'm thinking about getting a dog." in prompt
    assert "assistant: Nice! Do you have a breed in mind?" in prompt
    assert "1. A beagle, and I'll call her Luna." in prompt
    assert "Europe/Lisbon" in llm.calls[0].system


async def test_the_extract_writer_passes_the_assistants_reply_as_context():
    from memory_write_eval import ExtractOnly
    from scripted_llm import ScriptedStructured, answer

    llm = ScriptedStructured(answer({"proposals": []}))

    await ExtractOnly(llm).run(case("e016"), EVAL_NOW)

    assert (
        "<assistant_reply>\nYour sister is called Ana.\n</assistant_reply>" in llm.calls[0].prompt
    )


async def test_a_failed_extraction_fails_only_its_case():
    from memory_write_eval import ExtractOnly
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

    memory_write_eval.WRITERS["slow"] = Slow
    try:
        await memory_write_eval.main("dev", "slow", 1, [], tmp_path, concurrency=3)
    finally:
        del memory_write_eval.WRITERS["slow"]

    assert peak == 3


async def test_a_run_records_what_the_writer_proposed_and_the_report_prints_it(tmp_path, capsys):
    from memory_write_checks import Supersede

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
    memory_write_eval.print_failures([result])
    assert "wrote: replace city: [fact] Porto." in capsys.readouterr().out


async def test_a_run_carries_whether_its_case_needs_reconciliation():
    with_existing = await run_case(NoWrites(), case("e019"), 1)
    without = await run_case(NoWrites(), case("e010"), 1)
    failed = await run_case(Scripted(fail=RuntimeError("x")), case("e019"), 1)

    assert (with_existing.needs_reconciliation, without.needs_reconciliation) == (True, False)
    assert failed.needs_reconciliation is True


def test_the_summary_and_report_count_the_two_stages_apart(capsys):
    from memory_write_eval import CaseRun, print_report

    def row(needs, passed):
        return CaseRun("x", "add", 1, passed, (), (), (), 0, None, 0, 0, (), needs)

    summary = summarize([row(False, True), row(False, True), row(False, False), row(True, False)])

    assert summary["by_stage"] == {
        "alone": {"runs": 3, "passed": 2},
        "needs_reconciliation": {"runs": 1, "passed": 0},
    }
    print_report(summary)
    out = capsys.readouterr().out
    assert "no existing memories (the extractor alone)  2/3" in out
    assert "existing memories (need reconciliation)     0/1" in out


def test_an_empty_stage_is_zero_of_zero():
    assert summarize([])["by_stage"]["alone"] == {"runs": 0, "passed": 0}


# The pipeline writer: extract, then reconcile against the case's memories -----------------------


class WordEmbedder:
    """Vectors from word overlap, so similar sentences are near and no key is needed."""

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            vector = [0.0] * 128
            for word in text.lower().replace(".", "").split():
                vector[sum(map(ord, word)) % 128] += 1.0
            norm = sum(v * v for v in vector) ** 0.5
            out.append([v / norm for v in vector])
        return out


def proposals(*items):
    return answer({"proposals": list(items)})


def add_proposal(content, kind="fact", until=None):
    return {"op": "add", "kind": kind, "content": content, "valid_until": until}


def delete_proposal(content):
    return {"op": "delete", "kind": None, "content": content, "valid_until": None}


def verdict(decision, *ids):
    return answer({"decision": decision, "memory_ids": list(ids)}, Usage(100, 5))


def forget(*ids):
    return answer({"memory_ids": list(ids)}, Usage(60, 2))


async def play(case_id, *answers):
    from memory_write_eval import Pipeline
    from scripted_llm import ScriptedStructured

    llm = ScriptedStructured(*answers)
    outcome = await Pipeline(llm, WordEmbedder()).run(case(case_id), EVAL_NOW)
    return outcome, llm


async def test_a_changed_fact_replaces_the_stored_one():
    outcome, llm = await play(
        "e022", proposals(add_proposal("The owner lives in Porto.")), verdict("replaces", 0)
    )

    assert outcome.changes == [Supersede("city", "fact", "The owner lives in Porto.")]
    assert len(llm.calls) == 2


async def test_a_replacement_is_named_by_the_cases_key_not_its_position():
    outcome, _ = await play(
        "e024", proposals(add_proposal("The owner lives in Lisbon.")), verdict("replaces", 1)
    )

    assert outcome.changes == [Supersede("city", "fact", "The owner lives in Lisbon.")]


async def test_a_replacement_of_several_memories_keeps_the_extra_ones():
    outcome, _ = await play(
        "e025",
        proposals(add_proposal("The owner's phone provider is NOS.")),
        verdict("replaces", 0, 1),
    )

    (change,) = outcome.changes
    assert (change.target, change.also) == ("phone", ("dentist",))


async def test_a_repeat_changes_nothing_and_says_which_memory_had_it():
    outcome, _ = await play(
        "e019",
        proposals(add_proposal("The owner is vegetarian.", "preference")),
        verdict("same", 0),
    )

    assert outcome.changes == []
    assert any(note.startswith("noop ['diet']") for note in outcome.notes)


async def test_a_new_fact_with_nothing_near_is_added_with_one_model_call():
    outcome, llm = await play("e019", proposals(add_proposal("Silva treats my teeth.", "fact")))

    assert [type(c).__name__ for c in outcome.changes] == ["Add"]
    assert len(llm.calls) == 1


async def test_a_forget_request_deletes_the_memory_the_model_names():
    outcome, _ = await play(
        "e026",
        proposals(delete_proposal("The owner's knee surgery in March.")),
        forget(0),
    )

    assert outcome.changes == [Delete("surgery")]


async def test_a_forget_request_that_matches_nothing_deletes_nothing():
    outcome, _ = await play("e026", proposals(delete_proposal("The owner's cat.")))

    assert outcome.changes == []


async def test_an_event_end_survives_the_pipeline():
    outcome, _ = await play("e010", proposals(add_proposal("Dentist.", "event", "2027-03-03")))

    (change,) = outcome.changes
    assert change.valid_until is not None and change.valid_until.day == 4


async def test_tokens_of_both_steps_are_added_and_the_nearest_lookups_are_noted():
    outcome, _ = await play(
        "e022",
        proposals(add_proposal("The owner lives in Porto.")),
        verdict("replaces", 0),
    )

    assert (outcome.input_tokens, outcome.output_tokens) == (120, 13)
    assert any("nearest to 'The owner lives in Porto.': city 0." in n for n in outcome.notes)


async def test_a_proposal_the_answer_could_not_back_is_noted_as_dropped():
    outcome, _ = await play(
        "e022", proposals(add_proposal("The owner lives in Porto.")), verdict("replaces", 99)
    )

    assert outcome.changes == []
    assert any(note.startswith("dropped 1") for note in outcome.notes)


async def test_only_active_memories_are_candidates_and_vectors_are_kept_between_lookups():
    from memory_write_eval import InlineNeighbours

    embedder = WordEmbedder()
    inline = InlineNeighbours(case("e024"), embedder, {})  # one superseded, one active

    first = await inline.nearest("The owner lives in Lisbon.", 5)
    again = await inline.nearest("The owner lives in Lisbon.", 5)

    assert [inline.keys[c.id] for c in first] == ["city"] == [inline.keys[c.id] for c in again]
    assert first[0].similarity == pytest.approx(0.8, abs=0.01)
    assert len(embedder.calls) == 1  # the text and the stored memory, embedded once


async def test_a_case_with_no_stored_memory_embeds_nothing():
    from memory_write_eval import InlineNeighbours

    embedder = WordEmbedder()

    assert await InlineNeighbours(case("e010"), embedder, {}).nearest("x", 5) == []
    assert embedder.calls == []


async def test_candidates_come_back_nearest_first_and_limited():
    from memory_write_eval import InlineNeighbours

    inline = InlineNeighbours(case("e030"), WordEmbedder(), {})  # diet, dog, city

    found = await inline.nearest("The owner's dog is called Pepa.", 2)

    assert len(found) == 2 and inline.keys[found[0].id] == "dog"
    assert found[0].similarity >= found[1].similarity


async def test_the_pipeline_gives_reconciliation_the_owners_own_words():
    _, llm = await play(
        "e022", proposals(add_proposal("The owner lives in Porto.")), verdict("new")
    )

    assert "<owner_said>\nI moved to Porto last week.\n</owner_said>" in llm.calls[1].prompt
