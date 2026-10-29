import pytest
from agent_cases import is_past, load_all
from fake_embedder import FakeEmbedder
from memory_cases import EVAL_NOW
from memory_retrieval_cases import load_seed_index
from sqlalchemy import select

from app.memory.recall import (
    Recalled,
    asks_about_the_past,
    build_query,
    mark_used,
    recall,
)
from app.memory.seed import load_seed, seed
from app.models import Memory

NOW = EVAL_NOW


# The query and the past cue, no database ----------------------------------------------------


def test_the_query_is_the_last_two_owner_messages_oldest_first():
    assert build_query(["one", "two", "three"]) == "two\nthree"


def test_a_single_message_is_the_whole_query():
    assert build_query(["  Tell me about Tiago  "]) == "Tell me about Tiago"


def test_no_messages_make_an_empty_query():
    assert build_query([]) == ""


@pytest.mark.parametrize(
    "message",
    [
        "Where did I live before Porto?",
        "Did I use to drink my coffee black?",
        "Did I used to take the metro?",
        "What was my job previously?",
        "I moved two years ago, right?",
        "Did I ever have surgery?",
        "When was my dentist appointment in March?",
        "Who was my manager back then?",
        "Do I run in the mornings anymore?",
        "Did I run in the past?",
        "BEFORE the move, where was I?",
    ],
)
def test_a_question_about_the_past_is_noticed(message):
    assert asks_about_the_past(message)


@pytest.mark.parametrize(
    "message",
    [
        "Where do I live?",
        "What is my dog called?",
        "When is Tiago's birthday?",
        "What do I have coming up?",
        "Suggest a restaurant for dinner tonight.",
        "Whatever you think is best.",
        "I want to stay forever.",
        "",
    ],
)
def test_a_question_about_now_is_not_taken_for_the_past(message):
    assert not asks_about_the_past(message)


def test_the_cue_agrees_with_every_committed_agent_case():
    """A history case must ask in a way recall notices; a case that forbids a past memory must not."""
    seed_index = load_seed_index()
    for cases in load_all().values():
        for case in cases:
            cue = asks_about_the_past(case.message)
            if case.category == "history":
                assert cue, f"{case.id} is a history case the cue would miss"
            elif any(is_past(seed_index[key]) for key in case.must_not_recall):
                assert not cue, f"{case.id} forbids a past memory but would ask for the past"


# Recall against the seeded database -------------------------------------------------------


@pytest.fixture
async def seeded(session):
    ids = await seed(session, FakeEmbedder(), load_seed())
    return ids, {memory_id: key for key, memory_id in ids.items()}


async def recalled_keys(session, seeded, messages, **kwargs):
    _, by_id = seeded
    kwargs.setdefault("mode", "keyword")
    result = await recall(session, messages, now=NOW, embedder=FakeEmbedder(), **kwargs)
    return (
        [by_id[m.memory_id] for m in result.current],
        [by_id[m.memory_id] for m in result.past],
    )


async def test_a_question_about_now_recalls_current_memories_and_no_past(session, seeded):
    current, past = await recalled_keys(session, seeded, ["Where does the owner live? Porto"])

    assert current[0] == "home-city"
    assert past == []


async def test_a_question_about_the_past_adds_replaced_memories_in_their_own_list(session, seeded):
    current, past = await recalled_keys(
        session, seeded, ["Where did the owner live before? Lisbon Alfama"]
    )

    assert "home-city-old" in past
    assert "home-city-old" not in current
    assert not set(current) & set(past)


async def test_the_past_list_never_holds_a_memory_that_may_not_be_used(session, seeded):
    _, past = await recalled_keys(
        session,
        seeded,
        ["What did the owner have before? knee surgery Bruno laid off PT50 bank account"],
        history_limit=20,
    )

    assert not set(past) & {"surgery", "ex-partner", "layoff-worry", "ext-injection", "new-studio"}


async def test_an_ended_event_is_past_not_current(session, seeded):
    current, past = await recalled_keys(
        session, seeded, ["When was the dentist appointment in March?"]
    )

    assert "dentist-past" in past
    assert "dentist-past" not in current


async def test_a_follow_up_finds_the_person_named_in_the_message_before(session, seeded):
    current, _ = await recalled_keys(
        session, seeded, ["Tell me about Tiago", "And his birthday?"], limit=3
    )

    assert "partner" in current


async def test_only_the_last_two_messages_make_the_query(session, seeded):
    current, _ = await recalled_keys(
        session, seeded, ["Tiago partner", "something", "else entirely"], limit=3
    )

    assert "partner" not in current


async def test_the_limits_are_respected(session, seeded):
    current, past = await recalled_keys(
        session,
        seeded,
        ["Before, what did the owner like? coffee run work live"],
        limit=2,
        history_limit=1,
    )

    assert len(current) <= 2 and len(past) <= 1


async def test_nothing_to_ask_recalls_nothing(session, seeded):
    for messages in ([], [""], ["   "]):
        assert await recalled_keys(session, seeded, messages) == ([], [])


async def test_the_query_is_embedded_once_even_with_a_past_pass(session, seeded):
    embedder = FakeEmbedder()

    await recall(session, ["Where did I live before?"], now=NOW, embedder=embedder, mode="vector")

    assert len(embedder.calls) == 1


async def test_recall_alone_does_not_touch_last_used_at(session, seeded):
    await recalled_keys(session, seeded, ["Where did the owner live before? Porto Lisbon"])

    used = (await session.execute(select(Memory.last_used_at))).scalars().all()
    assert set(used) == {None}


async def test_mark_used_stamps_exactly_the_given_memories(session, seeded):
    ids, _ = seeded
    chosen = [ids["dog"], ids["home-city"]]

    await mark_used(session, chosen, NOW)

    rows = (await session.execute(select(Memory.id, Memory.last_used_at))).all()
    stamped = {memory_id for memory_id, used in rows if used is not None}
    assert stamped == set(chosen)
    assert {used for _, used in rows if used is not None} == {NOW}


async def test_marking_nothing_is_a_no_op(session, seeded):
    await mark_used(session, [], NOW)

    used = (await session.execute(select(Memory.last_used_at))).scalars().all()
    assert set(used) == {None}


def test_memory_ids_lists_current_then_past():
    from app.memory.search import SearchResult

    def hit(memory_id):
        return SearchResult(memory_id, "fact", "x", "owner", None, None, NOW, 0.0)

    assert Recalled(current=(hit(1), hit(2)), past=(hit(3),)).memory_ids == [1, 2, 3]
