"""Load the seed memories into the database, embedded, with a log entry for each.

    python -m app.memory.seed [--reset]

The file is ``data/seed_memories.yaml``. A memory already in the table (same content) is left
alone, so a second run writes nothing and embeds nothing; ``--reset`` empties the memory tables
first. The loader returns each memory's id by key, which is how the eval names them.

``snapshot`` and ``restore`` are for an eval whose cases can write memories: take a snapshot of the
seeded rows once (embeddings included), and put them back before every case with plain inserts, so
the embeddings API is not called again and every case starts from the same memory.
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.embeddings import Embedder
from app.models import Memory, MemoryEvent

SEED_FILE = Path(__file__).resolve().parents[3] / "data" / "seed_memories.yaml"


class InvalidSeed(ValueError):
    """The seed file is malformed or inconsistent."""


class SeedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    kind: Literal["fact", "preference", "event", "episode"]
    content: str
    status: Literal["active", "superseded", "deleted"] = "active"
    source_kind: Literal["owner", "external"] = "owner"
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    created_at: datetime
    superseded_by: str | None = None
    note: str = ""

    @field_validator("valid_from", "valid_until", "created_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value

    @model_validator(mode="after")
    def _superseded_names_its_replacement(self):
        if (self.status == "superseded") != (self.superseded_by is not None):
            raise ValueError(f"{self.key}: only a superseded memory names a replacement")
        return self


def load_seed(path: Path = SEED_FILE) -> list[SeedMemory]:
    try:
        rows = yaml.safe_load(path.read_text())["memories"]
        memories = [SeedMemory.model_validate(row) for row in rows]
    except (OSError, KeyError, TypeError, ValidationError, yaml.YAMLError) as error:
        raise InvalidSeed(f"{path.name}: {error}") from error
    by_key = {memory.key: memory for memory in memories}
    if len(by_key) != len(memories):
        raise InvalidSeed(f"{path.name}: duplicate keys")
    if len({memory.content for memory in memories}) != len(memories):
        raise InvalidSeed(f"{path.name}: duplicate content")
    for memory in memories:
        replacement = by_key.get(memory.superseded_by) if memory.superseded_by else None
        if memory.superseded_by and (replacement is None or replacement.status != "active"):
            raise InvalidSeed(f"{path.name}: {memory.key} names a missing or inactive replacement")
    return memories


async def seed(
    session: AsyncSession, embedder: Embedder, memories: list[SeedMemory]
) -> dict[str, int]:
    """Insert what is not there yet. Returns the id of every seed memory, by key."""
    rows = await session.execute(select(Memory.content, Memory.id))
    existing = {content: memory_id for content, memory_id in rows}
    new = [memory for memory in memories if memory.content not in existing]

    vectors = await embedder.embed([memory.content for memory in new]) if new else []
    created: dict[str, Memory] = {}
    for memory, vector in zip(new, vectors, strict=True):
        created[memory.key] = Memory(
            kind=memory.kind,
            content=memory.content,
            embedding=vector,
            source_kind=memory.source_kind,
            status=memory.status,
            valid_from=memory.valid_from,
            valid_until=memory.valid_until,
            created_at=memory.created_at,
        )
    session.add_all(created.values())
    await session.flush()

    ids = {
        memory.key: existing.get(memory.content) or created[memory.key].id for memory in memories
    }
    for memory in new:
        row = created[memory.key]
        if memory.superseded_by:
            row.superseded_by = ids[memory.superseded_by]
        session.add(MemoryEvent(memory_id=row.id, event="created", reason=f"seed: {memory.key}"))
        if memory.status != "active":
            session.add(
                MemoryEvent(memory_id=row.id, event=memory.status, reason=f"seed: {memory.key}")
            )
    await session.commit()
    return ids


async def reset(session: AsyncSession) -> None:
    await session.execute(text("TRUNCATE memory_events, memories RESTART IDENTITY CASCADE"))
    await session.commit()


_MEMORY_COLUMNS = (
    "id",
    "kind",
    "content",
    "embedding",
    "source_message_id",
    "source_kind",
    "status",
    "superseded_by",
    "valid_from",
    "valid_until",
    "created_at",
    "last_used_at",
)  # every column but ``tsv``, which the database computes from the content
_EVENT_COLUMNS = ("id", "memory_id", "event", "reason", "message_id", "created_at")


@dataclass(frozen=True)
class Snapshot:
    """The memory tables as they were: every row, with its id, embedding and log."""

    memories: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]


def _as_dicts(rows, columns: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    return tuple({name: getattr(row, name) for name in columns} for row in rows)


async def snapshot(session: AsyncSession) -> Snapshot:
    memories = (await session.execute(select(Memory).order_by(Memory.id))).scalars().all()
    events = (await session.execute(select(MemoryEvent).order_by(MemoryEvent.id))).scalars().all()
    return Snapshot(_as_dicts(memories, _MEMORY_COLUMNS), _as_dicts(events, _EVENT_COLUMNS))


async def restore(session: AsyncSession, saved: Snapshot) -> None:
    """Make the memory tables exactly what the snapshot was, ids included, and commit.

    Whatever was added, replaced or deleted since is undone. Rows go back with their own ids, so a
    key kept by id still names the same memory, and the id counters continue after the largest."""
    await session.execute(text("TRUNCATE memory_events, memories RESTART IDENTITY CASCADE"))
    # A replaced memory points at its replacement, which may come later, so the links go on last
    links = {row["id"]: row["superseded_by"] for row in saved.memories if row["superseded_by"]}
    session.add_all(Memory(**{**row, "superseded_by": None}) for row in saved.memories)
    await session.flush()
    for memory_id, replacement in links.items():
        await session.execute(
            text("UPDATE memories SET superseded_by = :replacement WHERE id = :id"),
            {"replacement": replacement, "id": memory_id},
        )
    session.add_all(MemoryEvent(**row) for row in saved.events)
    await session.flush()
    for table, rows in (("memories", saved.memories), ("memory_events", saved.events)):
        if rows:
            await session.execute(
                text("SELECT setval(pg_get_serial_sequence(:table, 'id'), :last)"),
                {"table": table, "last": max(row["id"] for row in rows)},
            )
    await session.commit()


async def main(do_reset: bool) -> None:
    from app.db import SessionLocal, engine
    from app.memory.embeddings import OpenAIEmbedder

    memories = load_seed()
    async with SessionLocal() as session:
        if do_reset:
            await reset(session)
        before = len((await session.execute(select(Memory.id))).all())
        await seed(session, OpenAIEmbedder(), memories)
        after = len((await session.execute(select(Memory.id))).all())
    await engine.dispose()
    print(f"{after - before} memories added, {after} in the table")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="empty the memory tables first")
    arguments = parser.parse_args()
    try:
        asyncio.run(main(arguments.reset))
    except InvalidSeed as error:
        raise SystemExit(f"error: {error}") from error
