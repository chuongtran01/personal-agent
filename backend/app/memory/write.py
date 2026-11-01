"""Changes to memory, each written together with its ``memory_events`` row.

Nothing is overwritten and nothing is removed: a change adds a row, or moves a row to another
status, and the event says what happened, when, why and from which message. The reasons are fixed
phrases that never quote a memory, so the log cannot keep text that was erased.

The four functions (``add_memory``, ``supersede``, ``delete``, ``restore``) flush and do not commit:
the caller owns the transaction, so an API handler writes one change and commits, and
``apply_decisions`` applies a whole turn's decisions and commits once. Rows are read ``FOR UPDATE``
and checked before anything is written, so a change that is refused leaves nothing behind, and an
owner's edit cannot race the extraction that follows a chat.

Status moves: ``active`` becomes ``superseded`` (replaced) or ``deleted``; ``superseded`` becomes
``deleted``; ``deleted`` is restored to what it was, ``active``, or ``superseded`` when something
replaced it first, because restoring the old fact next to its replacement would revive a stale one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.embeddings import Embedder
from app.memory.reconcile import Decision
from app.models import Memory, MemoryEvent


class MemoryNotFound(LookupError):
    """No memory has this id."""


class WrongStatus(ValueError):
    """The memory is not in a status this change can start from."""


@dataclass(frozen=True)
class Applied:
    added: tuple[int, ...]  # new rows, from adds and from replacements
    superseded: tuple[int, ...]  # rows that were replaced
    deleted: tuple[int, ...]
    skipped: int  # decisions that no longer applied, such as a memory the owner deleted meanwhile


def _clean(content: str) -> str:
    content = " ".join(content.split())
    if not content:
        raise ValueError("a memory cannot be empty")
    return content


def _log(
    db: AsyncSession,
    memory_id: int,
    event: str,
    reason: str,
    message_id: int | None,
    now: datetime,
) -> None:
    db.add(
        MemoryEvent(
            memory_id=memory_id, event=event, reason=reason, message_id=message_id, created_at=now
        )
    )


async def _lock(db: AsyncSession, ids: Sequence[int]) -> dict[int, Memory]:
    """The rows for these ids, locked in id order so two writers cannot deadlock."""
    found = await db.execute(
        select(Memory).where(Memory.id.in_(ids)).order_by(Memory.id).with_for_update()
    )
    rows = {memory.id: memory for memory in found.scalars()}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise MemoryNotFound(f"no memory {missing[0]}")
    return rows


async def add_memory(
    db: AsyncSession,
    *,
    kind: str,
    content: str,
    embedding: list[float],
    now: datetime,
    reason: str,
    message_id: int | None = None,
    valid_until: datetime | None = None,
) -> int:
    """A new owner memory, active, with its ``created`` event. Returns its id."""
    memory = Memory(
        kind=kind,
        content=_clean(content),
        embedding=embedding,
        source_message_id=message_id,
        source_kind="owner",
        status="active",
        valid_until=valid_until,
        created_at=now,
    )
    db.add(memory)
    await db.flush()
    _log(db, memory.id, "created", reason, message_id, now)
    await db.flush()
    return memory.id


async def supersede(
    db: AsyncSession,
    old_ids: Sequence[int],
    *,
    kind: str,
    content: str,
    embedding: list[float],
    now: datetime,
    reason: str,
    message_id: int | None = None,
    valid_until: datetime | None = None,
) -> int:
    """Replace active memories with a new one. Returns the new id.

    The old rows become ``superseded`` and point at the new one, and stop holding at ``now`` (or
    earlier, when they already had an end), so a question about the past still finds them."""
    ids = list(dict.fromkeys(old_ids))
    if not ids:
        raise ValueError("nothing to supersede")
    old = await _lock(db, ids)
    for memory in old.values():
        if memory.status != "active":
            raise WrongStatus(f"memory {memory.id} is {memory.status}, not active")

    new_id = await add_memory(
        db,
        kind=kind,
        content=content,
        embedding=embedding,
        now=now,
        reason=reason,
        message_id=message_id,
        valid_until=valid_until,
    )
    for memory in old.values():
        memory.status = "superseded"
        memory.superseded_by = new_id
        if memory.valid_until is None or memory.valid_until > now:
            memory.valid_until = now
        _log(db, memory.id, "superseded", reason, message_id, now)
    await db.flush()
    return new_id


async def delete(
    db: AsyncSession,
    ids: Sequence[int],
    *,
    now: datetime,
    reason: str,
    message_id: int | None = None,
) -> list[int]:
    """Mark memories deleted, whatever they were. Returns the ids that changed.

    Deleting what is already deleted changes nothing and logs nothing, so it is safe to repeat."""
    wanted = list(dict.fromkeys(ids))
    changed: list[int] = []
    for memory in (await _lock(db, wanted)).values():
        if memory.status == "deleted":
            continue
        memory.status = "deleted"
        _log(db, memory.id, "deleted", reason, message_id, now)
        changed.append(memory.id)
    await db.flush()
    return changed


async def restore(db: AsyncSession, memory_id: int, *, now: datetime, reason: str) -> str:
    """Bring a deleted memory back. Returns its new status: ``active``, or ``superseded`` when
    something had replaced it before it was deleted."""
    memory = (await _lock(db, [memory_id]))[memory_id]
    if memory.status != "deleted":
        raise WrongStatus(f"memory {memory_id} is {memory.status}, not deleted")
    memory.status = "superseded" if memory.superseded_by is not None else "active"
    _log(db, memory_id, "restored", reason, None, now)
    await db.flush()
    return memory.status


async def apply_decisions(
    db: AsyncSession,
    decisions: Sequence[Decision],
    *,
    embedder: Embedder,
    message_id: int | None,
    now: datetime,
) -> Applied:
    """Write what reconciliation decided, then commit once.

    Every new memory is embedded first, in one call: if that fails nothing is written. A decision
    whose memory changed since it was made (the owner deleted it, say) is skipped and counted, and
    the rest still apply, each in its own savepoint."""
    needs = [n for n, d in enumerate(decisions) if d.action in ("add", "supersede")]
    texts = [decisions[n].proposal.content for n in needs]
    vectors = await embedder.embed(texts) if texts else []
    embedding = dict(zip(needs, vectors, strict=True))  # by position in ``decisions``

    added: list[int] = []
    superseded: list[int] = []
    deleted: list[int] = []
    skipped = 0
    for position, decision in enumerate(decisions):
        if decision.action == "noop":
            continue
        proposal = decision.proposal
        try:
            async with db.begin_nested():
                if decision.action == "delete":
                    deleted += await delete(
                        db,
                        decision.memory_ids,
                        now=now,
                        reason=decision.reason,
                        message_id=message_id,
                    )
                    continue
                if proposal.kind is None:
                    raise ValueError("an add needs a kind")
                fields = {
                    "kind": proposal.kind,
                    "content": proposal.content,
                    "embedding": embedding[position],
                    "now": now,
                    "reason": decision.reason,
                    "message_id": message_id,
                    "valid_until": proposal.valid_until,
                }
                if decision.action == "add":
                    added.append(await add_memory(db, **fields))
                else:
                    added.append(await supersede(db, decision.memory_ids, **fields))
                    superseded += decision.memory_ids
        except (MemoryNotFound, WrongStatus):
            skipped += 1
    await db.commit()
    return Applied(tuple(added), tuple(superseded), tuple(deleted), skipped)
