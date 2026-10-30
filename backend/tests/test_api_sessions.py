import asyncio
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from fake_embedder import FakeEmbedder
from memory_cases import EVAL_NOW
from scripted_llm import ScriptedLLM, reply
from sqlalchemy import select, update

from app.agent import store
from app.agent.llm import LLMError, TextDelta, Usage
from app.agent.loop import AgentDone, AgentLoop, MemoryRecalled, TurnError
from app.agent.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from app.api import sse
from app.api.deps import get_agent
from app.auth import create_token
from app.config import settings
from app.db import get_session
from app.main import app
from app.memory.search import SearchResult
from app.memory.seed import load_seed, seed
from app.models import Memory

SECRET = "s" * 40


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    return create_token(SECRET, timedelta(minutes=5))


@pytest.fixture
def agent_for(session_factory):
    def make(*scripts):
        llm = ScriptedLLM(*scripts)
        loop = AgentLoop(
            session_factory,
            llm,
            FakeEmbedder(),
            clock=lambda: EVAL_NOW,
            recall_mode="keyword",
            retry_delay=0,
        )
        app.dependency_overrides[get_agent] = lambda: loop
        return loop, llm

    return make


@pytest.fixture
async def client(session_factory, token):
    async def db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"authorization": f"Bearer {token}"}
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def chat(client):
    return (await client.post("/sessions")).json()["id"]


def frames(response):
    """The (event, data) pairs of an SSE body."""
    out = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n")
        assert name.startswith("event: ") and data.startswith("data: ")
        out.append((name[7:], json.loads(data[6:])))
    return out


# Sessions ----------------------------------------------------------------------------------


async def test_a_new_chat_is_empty(client):
    response = await client.post("/sessions")

    assert response.status_code == 201
    body = response.json()
    assert body["message_count"] == 0 and body["preview"] is None and body["id"]


async def test_chats_are_listed_newest_first_with_a_preview(client, agent_for):
    agent_for(reply("ok"))
    first = (await client.post("/sessions")).json()["id"]
    second = (await client.post("/sessions")).json()["id"]
    await client.post(f"/sessions/{first}/messages", json={"text": "  a   long\nquestion "})

    listed = (await client.get("/sessions")).json()

    assert [c["id"] for c in listed] == [second, first]
    assert listed[1]["preview"] == "a long question" and listed[1]["message_count"] == 2
    assert listed[0]["preview"] is None


async def test_the_chat_list_has_a_limit(client):
    for _ in range(3):
        await client.post("/sessions")

    assert len((await client.get("/sessions?limit=2")).json()) == 2
    assert (await client.get("/sessions?limit=0")).status_code == 422
    assert (await client.get("/sessions?limit=201")).status_code == 422


# Sending a message -------------------------------------------------------------------------


async def test_a_message_streams_memory_then_text_then_done(
    client, chat, agent_for, session_factory
):
    async with session_factory() as db:
        await seed(db, FakeEmbedder(), load_seed())
    agent_for(reply("You live ", "in Porto.", usage=Usage(120, 30)))

    response = await client.post(
        f"/sessions/{chat}/messages", json={"text": "Where does the owner live? Porto"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = frames(response)
    assert [name for name, _ in events] == ["memory", "text", "text", "done"]
    memory = events[0][1]
    assert any("lives in Porto" in m["content"] for m in memory["current"])
    assert set(memory["current"][0]) == {"id", "kind", "content", "valid_from", "valid_until"}
    assert memory["past"] == []
    assert [data["text"] for name, data in events if name == "text"] == ["You live ", "in Porto."]
    done = events[-1][1]
    assert done["input_tokens"] == 120 and done["output_tokens"] == 30 and done["steps"] == 1


async def test_the_answer_is_stored_and_shown_in_the_chat(client, chat, agent_for):
    agent_for(reply("Hello, ", "owner."))

    await client.post(f"/sessions/{chat}/messages", json={"text": "hi"})
    shown = (await client.get(f"/sessions/{chat}/messages")).json()

    assert [(m["role"], m["text"]) for m in shown] == [
        ("user", "hi"),
        ("assistant", "Hello, owner."),
    ]
    assert shown[0]["id"] < shown[1]["id"] and shown[0]["created_at"]


async def test_the_chat_view_leaves_out_tool_calls_and_their_results(client, chat, session_factory):
    call = ToolCall(id="c1", name="lookup", arguments={})
    async with session_factory() as db:
        sid = uuid.UUID(chat)
        await store.append(db, sid, UserMessage(text="question"))
        await store.append(db, sid, AssistantMessage(text="", tool_calls=(call,)))
        await store.append(db, sid, ToolResultMessage(call_id="c1", output="secret output"))
        await store.append(db, sid, AssistantMessage(text="answer"))

    shown = (await client.get(f"/sessions/{chat}/messages")).json()

    assert [(m["role"], m["text"]) for m in shown] == [
        ("user", "question"),
        ("assistant", "answer"),
    ]


async def test_a_second_message_sees_the_first_turn(client, chat, agent_for):
    _, llm = agent_for(reply("one"), reply("two"))

    await client.post(f"/sessions/{chat}/messages", json={"text": "first"})
    await client.post(f"/sessions/{chat}/messages", json={"text": "second"})

    assert llm.calls[1].messages == (
        UserMessage(text="first"),
        AssistantMessage(text="one"),
        UserMessage(text="second"),
    )


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"text": "   "}, 422, "empty_message"),
        ({"text": "x" * 11}, 413, "message_too_long"),
    ],
)
async def test_a_message_that_cannot_be_taken_is_refused_before_a_stream_opens(
    client, chat, agent_for, session_factory, monkeypatch, body, status, code
):
    monkeypatch.setattr(settings, "max_message_chars", 10)
    agent_for()

    response = await client.post(f"/sessions/{chat}/messages", json=body)

    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert response.headers["content-type"] == "application/json"
    assert (await client.get(f"/sessions/{chat}/messages")).json() == []


async def test_a_message_to_a_chat_that_does_not_exist_is_a_404(client, agent_for):
    agent_for()

    response = await client.post(
        "/sessions/00000000-0000-0000-0000-000000000000/messages", json={"text": "hi"}
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_not_found"


async def test_a_bad_chat_id_or_body_is_a_422(client, agent_for):
    agent_for()

    assert (await client.post("/sessions/nope/messages", json={"text": "hi"})).status_code == 422
    chat = (await client.post("/sessions")).json()["id"]
    assert (await client.post(f"/sessions/{chat}/messages", json={})).status_code == 422
    assert (
        await client.post(f"/sessions/{chat}/messages", json={"text": "hi", "memory": False})
    ).status_code == 422


async def test_a_full_chat_is_a_409(client, chat, agent_for, monkeypatch):
    monkeypatch.setattr(settings, "max_session_messages", 2)
    agent_for(reply("ok"))
    await client.post(f"/sessions/{chat}/messages", json={"text": "one"})

    response = await client.post(f"/sessions/{chat}/messages", json={"text": "two"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "chat_full"


async def test_a_chat_that_is_still_answering_is_a_409(client, chat, agent_for):
    loop, _ = agent_for(reply("ok"))
    turn = await loop.start(uuid.UUID(chat), "still going")

    response = await client.post(f"/sessions/{chat}/messages", json={"text": "me too"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "turn_in_progress"
    turn.close()
    assert (await client.get(f"/sessions/{chat}/messages")).json()[0]["text"] == "still going"


async def test_a_failed_turn_streams_an_error_and_frees_the_chat(client, chat, agent_for):
    agent_for([LLMError("sk-secret rejected", retryable=False)], reply("back"))

    failed = await client.post(f"/sessions/{chat}/messages", json={"text": "hi"})
    retried = await client.post(f"/sessions/{chat}/messages", json={"text": "hi again"})

    events = frames(failed)
    assert [name for name, _ in events] == ["memory", "error"]
    assert events[-1][1]["code"] == "model_error" and "sk-secret" not in failed.text
    assert retried.status_code == 200 and frames(retried)[-1][0] == "done"


async def test_without_an_api_key_the_agent_is_unavailable(client, chat, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(app.state, "agent", None)

    response = await client.post(f"/sessions/{chat}/messages", json={"text": "hi"})

    assert response.status_code == 503
    assert "OPENAI" not in response.text


async def test_the_chat_view_of_an_unknown_chat_is_a_404(client):
    response = await client.get("/sessions/00000000-0000-0000-0000-000000000000/messages")

    assert response.status_code == 404


# What each reply recalled ---------------------------------------------------------------------


async def ask(client, chat, text):
    response = await client.post(f"/sessions/{chat}/messages", json={"text": text})
    return frames(response)


async def test_a_reloaded_chat_shows_the_memories_the_reply_streamed_with(
    client, chat, agent_for, seeded
):
    agent_for(reply("You live in Porto."))

    events = await ask(client, chat, "Where does the owner live? Porto")
    shown = (await client.get(f"/sessions/{chat}/messages")).json()

    streamed = events[0][1]["current"]
    user, answer = shown
    assert user["memories"] == []
    assert [m["id"] for m in answer["memories"]] == [m["id"] for m in streamed]
    assert answer["id"] == events[-1][1]["message_id"]
    first = answer["memories"][0]
    assert set(first) == {"id", "kind", "content", "past", "status"}
    assert first["content"] == streamed[0]["content"] and first["past"] is False
    assert first["status"] == "active"


async def test_a_past_memory_is_marked_as_one_that_was_true_once(client, chat, agent_for, seeded):
    agent_for(reply("Alfama."))

    await ask(client, chat, "Where did the owner live before? Lisbon Alfama")
    answer = (await client.get(f"/sessions/{chat}/messages")).json()[-1]

    past = [m for m in answer["memories"] if m["past"]]
    current = [m for m in answer["memories"] if not m["past"]]
    assert any("Alfama" in m["content"] and m["status"] == "superseded" for m in past)
    assert not {m["id"] for m in past} & {m["id"] for m in current}


async def test_a_memory_deleted_since_comes_back_without_its_text(
    client, chat, agent_for, seeded, session_factory
):
    agent_for(reply("Porto."))
    events = await ask(client, chat, "Where does the owner live? Porto")
    target = events[0][1]["current"][0]
    async with session_factory() as db:
        await db.execute(update(Memory).where(Memory.id == target["id"]).values(status="deleted"))
        await db.commit()

    answer = (await client.get(f"/sessions/{chat}/messages")).json()[-1]

    gone = next(m for m in answer["memories"] if m["id"] == target["id"])
    assert gone["status"] == "deleted" and gone["content"] is None
    assert target["content"] not in json.dumps(answer)


async def test_a_memory_replaced_since_keeps_its_text_and_says_so(
    client, chat, agent_for, seeded, session_factory
):
    agent_for(reply("Porto."))
    events = await ask(client, chat, "Where does the owner live? Porto")
    target = events[0][1]["current"][0]
    async with session_factory() as db:
        await db.execute(
            update(Memory).where(Memory.id == target["id"]).values(status="superseded")
        )
        await db.commit()

    answer = (await client.get(f"/sessions/{chat}/messages")).json()[-1]

    replaced = next(m for m in answer["memories"] if m["id"] == target["id"])
    assert replaced["status"] == "superseded" and replaced["content"] == target["content"]


async def test_a_reply_stored_before_recalls_were_kept_shows_no_memories(
    client, chat, session_factory
):
    async with session_factory() as db:
        sid = uuid.UUID(chat)
        await store.append(db, sid, UserMessage(text="hi"))
        await store.append(db, sid, AssistantMessage(text="hello"))

    shown = (await client.get(f"/sessions/{chat}/messages")).json()

    assert [m["memories"] for m in shown] == [[], []]


async def test_an_id_that_no_longer_resolves_is_left_out(client, chat, session_factory):
    async with session_factory() as db:
        sid = uuid.UUID(chat)
        await store.append(db, sid, UserMessage(text="hi"))
        await store.append(db, sid, AssistantMessage(text="hello", recalled=(987654,)))

    answer = (await client.get(f"/sessions/{chat}/messages")).json()[-1]

    assert answer["memories"] == []


# The turn outlives the client --------------------------------------------------------------


async def test_a_turn_nobody_reads_still_stores_the_answer_and_frees_the_chat(
    session_factory, chat
):
    loop = AgentLoop(
        session_factory,
        ScriptedLLM(reply("done anyway"), reply("next")),
        FakeEmbedder(),
        clock=lambda: EVAL_NOW,
        recall_mode="keyword",
    )
    sid = uuid.UUID(chat)
    tasks: set[asyncio.Task] = set()

    sse.run_in_background(await loop.start(sid, "hi"), tasks)  # the queue is never read
    await asyncio.gather(*tasks)

    async with session_factory() as db:
        assert (await store.load(db, sid))[-1] == AssistantMessage(text="done anyway")
    await loop.start(sid, "again")  # not refused: the chat was given back
    assert not tasks


async def test_an_unexpected_failure_ends_the_stream_with_an_error_and_frees_the_chat(
    session_factory, chat
):
    loop = AgentLoop(session_factory, ScriptedLLM(), FakeEmbedder(), recall_mode="keyword")

    async def broken(turn):
        raise RuntimeError("secret internals")
        yield

    loop._run = broken
    sid = uuid.UUID(chat)
    tasks: set[asyncio.Task] = set()

    queue = sse.run_in_background(await loop.start(sid, "hi"), tasks)
    frames_out = [frame async for frame in sse.frames(queue)]

    assert len(frames_out) == 1 and "internal_error" in frames_out[0]
    assert "secret internals" not in frames_out[0]
    await loop.start(sid, "again")


# Encoding ----------------------------------------------------------------------------------


def result(**kwargs):
    base = {
        "memory_id": 7,
        "kind": "fact",
        "content": "lives in Porto",
        "source_kind": "owner",
        "valid_from": None,
        "valid_until": None,
        "created_at": EVAL_NOW,
        "score": 0.5,
    }
    return SearchResult(**{**base, **kwargs})


def test_a_text_event_with_newlines_and_quotes_stays_on_one_data_line():
    frame = sse.encode(TextDelta('line one\nline "two"\n\n'))

    assert frame.count("\n") == 3 and frame.endswith("\n\n")
    assert json.loads(frame.split("data: ")[1])["text"] == 'line one\nline "two"\n\n'


def test_a_memory_event_carries_what_the_owner_may_want_to_see_and_no_scores_or_sources():
    frame = sse.encode(MemoryRecalled((result(),), (result(memory_id=8, valid_until=EVAL_NOW),)))
    data = json.loads(frame.split("data: ")[1])

    assert frame.startswith("event: memory\n")
    assert [m["id"] for m in data["current"]] == [7] and [m["id"] for m in data["past"]] == [8]
    assert data["past"][0]["valid_until"].startswith("2026")
    assert "score" not in data["current"][0] and "source_kind" not in data["current"][0]


def test_done_and_error_events_are_encoded():
    done = sse.encode(AgentDone(5, Usage(10, 4), 2, 0.25, 1.5))
    error = sse.encode(TurnError("recall_failed", "try again"))

    assert done.startswith("event: done\n")
    assert json.loads(done.split("data: ")[1]) == {
        "message_id": 5,
        "input_tokens": 10,
        "output_tokens": 4,
        "steps": 2,
        "recall_seconds": 0.25,
        "total_seconds": 1.5,
    }
    assert error.startswith("event: error\n")


# Memories ----------------------------------------------------------------------------------


@pytest.fixture
async def seeded(session_factory):
    async with session_factory() as db:
        await seed(db, FakeEmbedder(), load_seed())
        return (await db.execute(select(Memory.status, Memory.id))).all()


async def test_the_memory_list_shows_every_status_unless_one_is_asked_for(client, seeded):
    everything = (await client.get("/memories?limit=500")).json()
    active = (await client.get("/memories?status=active&limit=500")).json()
    deleted = (await client.get("/memories?status=deleted&limit=500")).json()

    assert len(everything) == len(seeded)
    assert {m["status"] for m in everything} == {"active", "superseded", "deleted"}
    assert {m["status"] for m in active} == {"active"}
    assert {m["status"] for m in deleted} == {"deleted"}


async def test_a_memory_row_has_its_dates_and_source_but_no_embedding(client, seeded):
    (first, *_) = (await client.get("/memories?limit=1")).json()

    assert set(first) == {
        "id",
        "kind",
        "content",
        "status",
        "source_kind",
        "superseded_by",
        "valid_from",
        "valid_until",
        "created_at",
        "last_used_at",
    }


async def test_the_memory_list_pages(client, seeded):
    page_one = (await client.get("/memories?limit=3")).json()
    page_two = (await client.get("/memories?limit=3&offset=3")).json()

    assert len(page_one) == len(page_two) == 3
    assert not {m["id"] for m in page_one} & {m["id"] for m in page_two}


@pytest.mark.parametrize("query", ["status=gone", "limit=0", "limit=501", "offset=-1"])
async def test_a_bad_memory_query_is_a_422(client, query):
    assert (await client.get(f"/memories?{query}")).status_code == 422


async def test_there_is_no_way_to_change_a_memory_here(client, seeded):
    first = (await client.get("/memories?limit=1")).json()[0]["id"]

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = await client.request(method, f"/memories/{first}", json={})
        assert response.status_code in (404, 405)
