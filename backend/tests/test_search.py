import math
from datetime import UTC, datetime, timedelta

import pytest
from fake_embedder import FakeEmbedder
from memory_cases import EVAL_NOW

from app.memory.search import (
    SearchResult,
    apply_recency,
    fuse_results,
    recency_score,
    reciprocal_rank_fusion,
    search,
)
from app.memory.seed import load_seed, seed

NOW = EVAL_NOW


def result(memory_id, score=0.0, created_at=None, valid_from=None, **scores):
    return SearchResult(
        memory_id=memory_id,
        kind="fact",
        content=f"memory {memory_id}",
        source_kind="owner",
        valid_from=valid_from,
        valid_until=None,
        created_at=created_at or NOW,
        score=score,
        **scores,
    )


# Fusion and recency, no database ---------------------------------------------------------


def test_rrf_scores_each_list_by_reciprocal_rank():
    fused = dict(reciprocal_rank_fusion([[1, 2], [2, 3]], k=60))

    assert fused[1] == pytest.approx(1 / 61)
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[3] == pytest.approx(1 / 62)


def test_rrf_ranks_an_item_in_both_lists_first():
    assert reciprocal_rank_fusion([[1, 2], [3, 2]])[0][0] == 2


def test_rrf_with_one_empty_list_keeps_the_other_order():
    assert [item for item, _ in reciprocal_rank_fusion([[5, 4, 6], []])] == [5, 4, 6]


def test_rrf_of_nothing_is_empty():
    assert reciprocal_rank_fusion([[], []]) == []


def test_fuse_results_keeps_both_scores_for_a_shared_memory():
    keyword = [result(1, keyword_score=0.4), result(2, keyword_score=0.1)]
    vector = [result(2, vector_score=0.9), result(3, vector_score=0.8)]

    fused = fuse_results(keyword, vector)

    assert [r.memory_id for r in fused] == [2, 1, 3]
    assert fused[0].keyword_score == 0.1 and fused[0].vector_score == 0.9


def test_a_memory_made_now_has_full_recency():
    assert recency_score(NOW, None, NOW, 90) == 1


def test_recency_fades_by_the_half_life():
    created = NOW - timedelta(days=90)

    assert recency_score(created, None, NOW, 90) == pytest.approx(math.exp(-1))


def test_recency_counts_from_when_the_fact_started_to_hold():
    old_row_new_fact = recency_score(NOW - timedelta(days=300), NOW - timedelta(days=1), NOW, 90)

    assert old_row_new_fact == pytest.approx(math.exp(-1 / 90))


def test_a_fact_that_starts_later_counts_as_age_zero():
    assert recency_score(NOW, NOW + timedelta(days=30), NOW, 90) == 1


def test_recency_can_reorder_close_results_but_not_distant_ones():
    old = result(1, 0.0320, created_at=NOW - timedelta(days=400))
    new = result(2, 0.0318, created_at=NOW - timedelta(days=2))
    far = result(3, 0.0100, created_at=NOW)

    ranked = apply_recency([old, new, far], NOW, weight=0.005, half_life_days=90)

    assert [r.memory_id for r in ranked] == [2, 1, 3]
    assert ranked[0].recency_score > ranked[1].recency_score


# Search against the seeded database --------------------------------------------------------


@pytest.fixture
async def seeded(session):
    ids = await seed(session, FakeEmbedder(), load_seed())
    return {memory_id: key for key, memory_id in ids.items()}, {m.key: m for m in load_seed()}


MODES = ["keyword", "vector", "hybrid", "hybrid_recency"]
INACTIVE_OR_HIDDEN = {
    "superseded": ["home-city-old", "job-old", "coffee-old", "run-time-old", "commute-old"],
    "deleted": ["surgery", "ex-partner", "layoff-worry"],
    "past": ["dentist-past", "berlin-conference", "marta-wedding", "faro-flight"],
    "external": [
        "ext-climbing-night",
        "ext-electricity-bill",
        "ext-design-review",
        "ext-injection",
    ],
    "not yet true": ["new-studio"],
}


async def keys_for(session, seeded, query, **kwargs):
    by_id, _ = seeded
    found = await search(session, query, now=NOW, embedder=FakeEmbedder(), limit=60, **kwargs)
    return [by_id[r.memory_id] for r in found]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("group", INACTIVE_OR_HIDDEN)
async def test_a_memory_that_may_not_be_used_never_comes_back(session, seeded, mode, group):
    _, by_key = seeded
    for key in INACTIVE_OR_HIDDEN[group]:
        # Asking in the memory's own words is the hardest case for a leak
        found = await keys_for(session, seeded, by_key[key].content, mode=mode)

        assert key not in found, f"{key} leaked from {mode}"


@pytest.mark.parametrize("mode", MODES)
async def test_a_search_with_no_match_in_the_usable_set_still_obeys_the_filter(
    session, seeded, mode
):
    found = await keys_for(session, seeded, "bank account PT50 instruction", mode=mode)

    assert "ext-injection" not in found


async def test_external_memories_come_back_only_when_asked_for_and_say_where_from(session, seeded):
    by_id, _ = seeded

    found = await search(
        session,
        "electricity bill due",
        now=NOW,
        mode="hybrid",
        embedder=FakeEmbedder(),
        include_external=True,
        limit=60,
    )

    external = [r for r in found if by_id[r.memory_id] == "ext-electricity-bill"]
    assert [r.source_kind for r in external] == ["external"]


async def test_a_fact_that_starts_later_comes_back_when_upcoming_is_asked_for(session, seeded):
    found = await keys_for(
        session, seeded, "new design studio 4 January", mode="keyword", include_upcoming=True
    )

    assert "new-studio" in found


async def test_future_events_are_retrievable_without_any_flag(session, seeded):
    found = await keys_for(session, seeded, "dentist check-up November", mode="keyword")

    assert "dentist-future" in found
    assert "dentist-past" not in found


async def test_keyword_search_finds_a_memory_by_its_words(session, seeded):
    found = await keys_for(session, seeded, "Where does the owner live? Porto", mode="keyword")

    assert found[0] == "home-city"


async def test_vector_search_ranks_the_memory_with_the_same_text_first(session, seeded):
    _, by_key = seeded

    found = await keys_for(session, seeded, by_key["dog"].content, mode="vector")

    assert found[0] == "dog"


async def test_the_limit_is_respected(session, seeded):
    found = await search(session, "owner", now=NOW, mode="hybrid", embedder=FakeEmbedder(), limit=3)

    assert len(found) == 3


async def test_recency_puts_the_newest_of_three_wordings_first(session, seeded):
    plain = await keys_for(session, seeded, "still water", mode="hybrid")
    favoured = await keys_for(
        session, seeded, "still water", mode="hybrid_recency", recency_weight=1.0
    )

    water = [k for k in favoured if k.startswith("water-")]
    assert water[0] == "water-3"
    assert set(water) == {k for k in plain if k.startswith("water-")}


async def test_a_mode_that_needs_an_embedder_refuses_without_one(session):
    for mode in ("vector", "hybrid", "hybrid_recency"):
        with pytest.raises(ValueError, match="needs an embedder"):
            await search(session, "q", now=NOW, mode=mode)


async def test_the_clock_decides_what_has_already_happened(session, seeded):
    before = datetime(2026, 3, 1, tzinfo=UTC)
    by_id, _ = seeded

    found = await search(
        session, "dentist appointment 3 March", now=before, mode="keyword", limit=60
    )

    assert "dentist-past" in [by_id[r.memory_id] for r in found]


async def test_each_include_flag_lifts_its_own_filter(session, seeded):
    superseded = await keys_for(
        session, seeded, "lives in Lisbon Alfama", mode="keyword", include_inactive=True
    )
    ended = await keys_for(
        session, seeded, "dentist appointment 3 March", mode="keyword", include_ended=True
    )
    default = await keys_for(session, seeded, "lives in Lisbon Alfama", mode="keyword")

    assert "home-city-old" in superseded
    assert "dentist-past" in ended
    assert "home-city-old" not in default
    # Lifting one filter lifts only that one
    assert "dentist-past" not in await keys_for(
        session, seeded, "dentist appointment 3 March", mode="keyword", include_inactive=True
    )


# History mode ----------------------------------------------------------------------------

REPLACED = ["home-city-old", "job-old", "coffee-old", "run-time-old", "commute-old"]
ENDED = ["dentist-past", "berlin-conference", "marta-wedding", "faro-flight"]
NEVER_PAST = ["surgery", "ex-partner", "layoff-worry", "ext-injection", "ext-electricity-bill"]


@pytest.mark.parametrize("mode", MODES)
async def test_history_returns_only_replaced_memories_and_ended_events(session, seeded, mode):
    found = set(await keys_for(session, seeded, "owner", mode=mode, history=True))

    assert found and found <= set(REPLACED + ENDED)
    if mode != "keyword":  # keyword search only returns memories that share a term
        assert found == set(REPLACED + ENDED)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("key", NEVER_PAST + ["new-studio", "home-city", "dentist-future"])
async def test_history_never_returns_deleted_external_upcoming_or_current_memories(
    session, seeded, mode, key
):
    _, by_key = seeded

    found = await keys_for(session, seeded, by_key[key].content, mode=mode, history=True)

    assert key not in found


async def test_the_default_search_still_hides_the_past(session, seeded):
    found = set(await keys_for(session, seeded, "owner", mode="vector"))

    assert not found & set(REPLACED + ENDED)


async def test_history_ranks_the_matching_replaced_memory_first(session, seeded):
    _, by_key = seeded

    found = await keys_for(
        session, seeded, by_key["home-city-old"].content, mode="vector", history=True
    )

    assert found[0] == "home-city-old"


async def test_history_follows_the_clock(session, seeded):
    by_id, _ = seeded
    before = datetime(2026, 3, 1, tzinfo=UTC)

    found = await search(
        session,
        "dentist appointment 3 March",
        now=before,
        mode="keyword",
        history=True,
        limit=60,
    )

    # Before 3 March 2026 the appointment had not happened yet
    assert "dentist-past" not in [by_id[r.memory_id] for r in found]


@pytest.mark.parametrize("flag", ["include_inactive", "include_ended"])
async def test_history_cannot_be_combined_with_the_control_flags(session, flag):
    with pytest.raises(ValueError, match="cannot be combined"):
        await search(session, "q", now=NOW, mode="keyword", history=True, **{flag: True})
