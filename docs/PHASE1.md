# Phase 1: Foundation

**Status:** not started
**Gate:** schema loads, `/health` ok

## Goal

Stand up the repo, the database and a minimal backend so later phases have a place to land. No agent, retrieval or memory logic yet. This phase also writes down the memory traps and sets up an empty eval harness, so the evals exist before the code they test.

## Deliverables

| Deliverable | Location | Notes |
| --- | --- | --- |
| Git repo | repo root | Own repository from the start (Northwind began inside the home-directory repo and had to move); `.gitignore` covers `.env`, `.venv`, `evals/results/` |
| Database container | `docker-compose.yml` | `pgvector/pgvector:pg16`, a host port that does not clash with Northwind (5434), named volume `pgdata`, healthcheck |
| Schema | `backend/app/models.py`, `backend/migrations/` | Six tables, created by Alembic migration `0001` |
| Backend project | `backend/pyproject.toml` | Python 3.12+, FastAPI, SQLAlchemy 2.0 (async), Alembic, psycopg 3, pgvector, OpenAI, Langfuse; `dev` extras for pytest and ruff |
| Settings | `backend/app/config.py` | Reads `.env`; database URL, model names, embedding dimensions |
| DB access | `backend/app/db.py` | Async engine and session factory; `get_session` dependency |
| API stub | `backend/app/main.py` | `GET /health` runs `SELECT 1` against the pool |
| Package layout | `backend/app/{agent,memory,tools,api}/` | Empty packages, filled in later phases |
| Memory traps file | `data/memory_traps.yaml` | The eight traps from `DESIGN.md`, each with a scenario, a probe and the expected behavior |
| Eval skeleton | `evals/` | `datasets/` with the schema for a multi-session case (turns, gap, probe, check), a runner that loads and validates a dataset and runs zero cases |
| Env template | `.env.example` | Database, OpenAI, model names, Langfuse |
| Dev commands | `Makefile` | `up`, `down`, `db-reset`, `migrate`, `install`, `test`, `lint` |
| Git hooks | `.pre-commit-config.yaml` or `.githooks/` | ruff and tests before commit, as in Northwind |

## Schema

| Group | Tables |
| --- | --- |
| Memory | `memories`, `memory_events` |
| Conversations | `chat_sessions`, `chat_messages` |
| Tool calls | `idempotency_keys`, `tool_audit` |

Design points that later phases depend on:

- `memories.status` is `active`, `superseded` or `deleted`, and `superseded_by` points to the newer row. A change adds a row; nothing is overwritten.
- `memories.source_kind` is `owner` or `external`, so text from an email can never become an owner-level fact.
- `memories.valid_from` and `valid_until` let retrieval skip facts that are no longer true, and events that already happened.
- `memories.embedding` is `vector(1536)` with an HNSW cosine index; `memories.tsv` is a generated `tsvector` column with a GIN index.
- `memory_events` is append-only: every write, update and deletion with its reason.
- `chat_messages.content` is JSONB so tool calls and results can be replayed exactly. `chat_sessions` belongs to one owner; there is no multi-user model.
- `idempotency_keys` and `tool_audit` are created now but stay empty until phase 6, so the schema does not change under data later.

## Memory traps file

`data/memory_traps.yaml` is the ground-truth manifest, like `traps.yaml` in Northwind. Each entry has an id, the turns to say, the gap between sessions, the probe question and the expected behavior. Phase 4 builds the eval set from it so data and tests stay in sync.

| Trap | Expected behavior |
| --- | --- |
| Changed fact | New fact answered; old one kept as history |
| Conflicting statements in one chat | The later statement wins |
| Forget request | Not recalled next session; the probe does not leak it |
| Irrelevant memory | Not mentioned |
| Injection from an email | Nothing stored as an owner fact |
| Stale event | Not offered as upcoming |
| Sensitive content | Not stored |
| Near-duplicate | One memory, not five |

## Run it

```sh
cp .env.example .env
make up          # starts Postgres on localhost:5434
make install     # creates backend/.venv and installs dependencies
make migrate     # applies Alembic migrations
make test
cd backend && .venv/bin/uvicorn app.main:app --reload
curl localhost:8000/health
```

## Decisions to settle before starting

| Decision | Default if you say nothing |
| --- | --- |
| Project name and repo name | `personal-agent` |
| Chat surface | Terminal client in phase 3; web UI only if a demo needs it |
| Layout | `backend/`, `evals/`, `data/`, `docs/`, same as Northwind |
| Database port | 5434 |
| Tooling | pip, ruff, pytest, same Makefile and hooks as Northwind |

## Gate check

| Check | Expected result |
| --- | --- |
| Schema loads on an empty volume | Six tables created by `alembic upgrade head`; `vector` extension present |
| `GET /health` | Returns `{"status": "ok"}` against the fresh database |
| Traps file | Loads and validates; eight entries |
| Eval runner | Loads the empty dataset and exits cleanly |
| `pytest` | Model, config, health and traps-file tests pass |

## Not in this phase

- Embedding, search or any memory write
- Agent loop, sessions and auth
- Chat surface
- MCP client or servers
- Seed data beyond the traps file
