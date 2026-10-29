import memory_retrieval_eval as runner
import pytest
from fake_embedder import FakeEmbedder
from memory_retrieval_cases import RetrievalCase, load_all
from memory_retrieval_eval import evaluate_case, summarize

from app.memory.seed import load_seed, seed

SEED = {m.key: m for m in load_seed()}


def case(**fields):
    return RetrievalCase(
        **{
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
        | fields
    )


def ranked(*keys):
    return [(key, 1 / (rank + 1)) for rank, key in enumerate(keys, 1)]


def test_the_rank_of_the_first_relevant_memory_is_found():
    outcome = evaluate_case(case(), SEED, ranked("dog", "job", "home-city"))

    assert outcome.first_rank == 3
    assert outcome.all_found is True
    assert outcome.leaks == []


def test_nothing_relevant_in_the_results_has_no_rank():
    outcome = evaluate_case(case(), SEED, ranked("dog", "job"))

    assert outcome.first_rank is None
    assert outcome.all_found is False


def test_all_found_needs_every_relevant_memory_in_the_top_five():
    both = case(relevant_keys=["partner", "christmas-gift"], forbidden_keys=[])

    inside = evaluate_case(both, SEED, ranked("partner", "dog", "job", "diet", "christmas-gift"))
    outside = evaluate_case(
        both, SEED, ranked("partner", "dog", "job", "diet", "books", "christmas-gift")
    )

    assert inside.all_found is True
    assert outside.all_found is False
    assert outside.first_rank == 1


def test_a_forbidden_memory_is_a_leak_wherever_it_appears():
    outcome = evaluate_case(case(), SEED, ranked("home-city", "dog", "home-city-old"))

    assert outcome.leaks == ["home-city-old"]
    assert outcome.first_rank == 1


def test_a_memory_the_search_may_not_return_is_a_leak_even_if_the_row_does_not_forbid_it():
    outcome = evaluate_case(
        case(forbidden_keys=[]), SEED, ranked("home-city", "surgery", "ext-injection")
    )

    assert outcome.leaks == ["surgery", "ext-injection"]


def test_an_external_memory_is_not_a_leak_when_the_row_asks_for_it():
    asked = case(relevant_keys=["ext-design-review"], forbidden_keys=[], include_external=True)

    assert evaluate_case(asked, SEED, ranked("ext-design-review")).leaks == []


def test_summary_leaves_unanswerable_questions_out_of_recall_but_counts_their_leaks():
    answerable = evaluate_case(case(), SEED, ranked("home-city"))
    nothing = evaluate_case(
        case(id="m901", relevant_keys=[], forbidden_keys=["surgery"], expect_no_answer=True),
        SEED,
        ranked("dog", "surgery"),
    )

    summary = summarize([answerable, nothing])

    assert summary["questions"] == 1
    assert summary["recall@5"] == 1
    assert summary["leaks"] == 1
    assert summary["top_score"]["no_answer"]["median"] == pytest.approx(0.5)


def test_summary_reports_recall_by_category():
    hit = evaluate_case(case(), SEED, ranked("home-city"))
    miss = evaluate_case(case(id="m901", category="direct"), SEED, ranked("dog"))

    by_category = summarize([hit, miss])["by_category"]

    assert by_category["changed_fact"]["recall@5"] == 1
    assert by_category["direct"]["recall@5"] == 0


async def test_the_search_leaks_nothing_on_either_split_in_any_mode(session):
    memories = load_seed()
    embedder = FakeEmbedder()
    ids = await seed(session, embedder, memories)
    seed_memories = {m.key: m for m in memories}
    cases = [c for split in load_all().values() for c in split]

    outcomes = await runner.run(session, cases, list(runner.MODES), embedder, seed_memories, ids)

    assert set(outcomes) == set(runner.MODES)
    assert {len(rows) for rows in outcomes.values()} == {len(cases)}
    assert [o.leaks for rows in outcomes.values() for o in rows if o.leaks] == []


async def test_keyword_search_finds_exact_term_questions(session):
    memories = load_seed()
    ids = await seed(session, FakeEmbedder(), memories)
    exact = [c for c in load_all()["dev"] if c.category == "exact_term"]

    outcomes = await runner.run(
        session, exact, ["keyword"], None, {m.key: m for m in memories}, ids
    )

    assert all(o.first_rank == 1 for o in outcomes["keyword"])


async def test_the_control_gets_the_forbidden_memories_back_when_the_filters_are_lifted(session):
    memories = load_seed()
    ids = await seed(session, FakeEmbedder(), memories)
    seed_memories = {m.key: m for m in memories}
    traps = [c for c in load_all()["dev"] + load_all()["test"] if c.question == "Where do I live?"]
    assert traps, "expected the changed-fact question in the dataset"

    filtered = await runner.run(session, traps, ["keyword"], None, seed_memories, ids)
    lifted = await runner.run(session, traps, ["keyword"], None, seed_memories, ids, control=True)

    assert runner.summarize_control(traps, filtered["keyword"])["forbidden_returned"] == 0
    assert runner.summarize_control(traps, lifted["keyword"])["forbidden_returned"] >= 1
