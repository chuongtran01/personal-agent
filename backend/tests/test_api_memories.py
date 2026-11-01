from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fake_embedder import FakeEmbedder
from sqlalchemy import select, update

from app.api.deps import get_now
from app.auth import create_token
from app.config import settings
from app.db import get_session
from app.main import app
from app.memory.search import search
from app.memory.write import add_memory, delete, supersede
from app.models import Memory

SECRET = "s" * 40
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(days=10)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    return create_token(SECRET, timedelta(minutes=5))


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
async def client(session_factory, token):
    async def db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_now] = lambda: NOW
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"authorization": f"Bearer {token}"}
    ) as client:
        yield client
    app.dependency_overrides.clear()


async def make(session_factory, embedder, content="The owner lives in Lisbon.", **change):
    status = change.pop("status", "active")
    fields = {"kind": "fact", "now": EARLIER, "reason": "the owner said it", **change}
    async with session_factory() as db:
        vector = (await embedder.embed([content]))[0]
        memory_id = await add_memory(db, content=content, embedding=vector, **fields)
        if status != "active":
            await db.execute(update(Memory).where(Memory.id == memory_id).values(status=status))
        await db.commit()
    return memory_id


async def replace(session_factory, embedder, old_id, content="The owner lives in Porto."):
    async with session_factory() as db:
        vector = (await embedder.embed([content]))[0]
        new_id = await supersede(
            db, [old_id], kind="fact", content=content, embedding=vector, now=NOW, reason="r"
        )
        await db.commit()
    return new_id


async def statuses(session_factory):
    async with session_factory() as db:
        found = await db.execute(select(Memory).order_by(Memory.id))
        return [(m.id, m.content, m.status) for m in found.scalars()]


async def recalled(session_factory, embedder, text):
    async with session_factory() as db:
        found = await search(db, text, now=NOW, mode="vector", embedder=embedder, limit=10)
    return [r.memory_id for r in found]


# Sign-in ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"), [("DELETE", "/memories/1"), ("GET", "/memories/1/events")]
)
async def test_every_memory_endpoint_needs_the_owners_token(client, method, path):
    response = await client.request(method, path, headers={"authorization": ""})

    assert response.status_code == 401


# What there is not -----------------------------------------------------------------------------


async def test_a_memory_cannot_be_edited_restored_created_or_replaced_through_the_api(
    client, session_factory, embedder
):
    memory_id = await make(session_factory, embedder, status="deleted")

    attempts = [
        await client.patch(f"/memories/{memory_id}", json={"content": "new"}),
        await client.put(f"/memories/{memory_id}", json={"content": "new"}),
        await client.post(f"/memories/{memory_id}", json={"content": "new"}),
        await client.post(f"/memories/{memory_id}/restore"),
    ]

    assert [r.status_code for r in attempts] == [405, 405, 405, 404]
    assert await statuses(session_factory) == [(memory_id, "The owner lives in Lisbon.", "deleted")]


# Delete ----------------------------------------------------------------------------------------


async def test_a_delete_marks_the_memory_and_keeps_its_text(client, session_factory, embedder):
    memory_id = await make(session_factory, embedder)

    response = await client.delete(f"/memories/{memory_id}")

    assert response.status_code == 204 and response.content == b""
    assert await statuses(session_factory) == [(memory_id, "The owner lives in Lisbon.", "deleted")]
    events = (await client.get(f"/memories/{memory_id}/events")).json()
    assert (events[-1]["event"], events[-1]["reason"]) == (
        "deleted",
        "deleted by the owner in the app",
    )
    listed = (await client.get("/memories?status=deleted")).json()
    assert [m["id"] for m in listed] == [memory_id]


async def test_a_deleted_memory_is_no_longer_recalled(client, session_factory, embedder):
    memory_id = await make(session_factory, embedder)
    assert await recalled(session_factory, embedder, "The owner lives in Lisbon.") == [memory_id]

    await client.delete(f"/memories/{memory_id}")

    assert await recalled(session_factory, embedder, "The owner lives in Lisbon.") == []


async def test_deleting_twice_is_fine_and_logs_once(client, session_factory, embedder):
    memory_id = await make(session_factory, embedder)

    first = await client.delete(f"/memories/{memory_id}")
    second = await client.delete(f"/memories/{memory_id}")

    assert (first.status_code, second.status_code) == (204, 204)
    assert len((await client.get(f"/memories/{memory_id}/events")).json()) == 2


async def test_a_replaced_memory_can_be_deleted_too(client, session_factory, embedder):
    old = await make(session_factory, embedder)
    new = await replace(session_factory, embedder, old)

    assert (await client.delete(f"/memories/{old}")).status_code == 204

    assert [(i, s) for i, _, s in await statuses(session_factory)] == [
        (old, "deleted"),
        (new, "active"),
    ]


async def test_deleting_an_unknown_memory_is_a_404(client):
    response = await client.delete("/memories/999")

    assert response.status_code == 404 and response.json()["detail"]["code"] == "memory_not_found"


# Events ----------------------------------------------------------------------------------------


async def test_the_events_come_oldest_first_with_their_reasons(client, session_factory, embedder):
    memory_id = await make(session_factory, embedder)
    async with session_factory() as db:
        await delete(db, [memory_id], now=NOW, reason="the owner asked to forget it")
        await db.commit()

    events = (await client.get(f"/memories/{memory_id}/events")).json()

    assert [(e["event"], e["reason"]) for e in events] == [
        ("created", "the owner said it"),
        ("deleted", "the owner asked to forget it"),
    ]
    assert events[0]["id"] < events[1]["id"]
    assert set(events[0]) == {"id", "event", "reason", "message_id", "created_at"}


async def test_the_events_of_a_replaced_memory_include_the_replacement(
    client, session_factory, embedder
):
    old = await make(session_factory, embedder)
    await replace(session_factory, embedder, old)

    events = (await client.get(f"/memories/{old}/events")).json()

    assert [e["event"] for e in events] == ["created", "superseded"]


async def test_the_events_of_an_unknown_memory_are_a_404(client):
    response = await client.get("/memories/999/events")

    assert response.status_code == 404 and response.json()["detail"]["code"] == "memory_not_found"


def test_the_default_clock_is_the_current_utc_time():
    moment = get_now()

    assert moment.tzinfo is not None
    assert abs((datetime.now(UTC) - moment).total_seconds()) < 5
