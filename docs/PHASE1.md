# Phase 1: Foundation

**Status:** done
**Gate:** schema loads, `/health` ok

## Goal

Stand up the repo, the database and a minimal backend so later phases have a place to land. No agent, retrieval or memory logic yet. This phase also writes down the memory traps and sets up an empty eval harness, so the evals exist before the code they test.

## Deliverables

- [x] **Git repo** (repo root): Own repository from the start; `.gitignore` covers `.env`, `.venv`, `evals/results/`
- [x] **Database container** (`docker-compose.yml`): `pgvector/pgvector:pg16`, host port 5432 (set `DB_PORT` in `.env` if another database already uses it), named volume `pgdata`, healthcheck
- [x] **Schema** (`backend/app/models.py`, `backend/migrations/`): Six tables, created by Alembic migration `0001`
- [x] **Backend project** (`backend/pyproject.toml`): Python 3.12+, FastAPI, SQLAlchemy 2.0 (async), Alembic, psycopg 3, pgvector, OpenAI, Langfuse; `dev` extras for pytest and ruff
- [x] **Settings** (`backend/app/config.py`): Reads `.env`; database URL, model names, embedding dimensions
- [x] **DB access** (`backend/app/db.py`): Async engine and session factory; `get_session` dependency
- [x] **API stub** (`backend/app/main.py`): `GET /health` runs `SELECT 1` against the pool
- [x] **Package layout** (`backend/app/{agent,memory,tools,api}/`): Empty packages, filled in later phases
- [x] **Memory traps file** (`data/memory_traps.yaml`): The eight traps from `DESIGN.md`, each with a scenario, a probe and the expected behavior
- [x] **Eval skeleton** (`evals/`): `datasets/` with the schema for a multi-session case (turns, gap, probe, check), a runner that loads and validates a dataset and runs zero cases
- [x] **Env template** (`.env.example`): Database, OpenAI, model names, Langfuse
- [x] **Dev commands** (`Makefile`): `up`, `down`, `db-reset`, `migrate`, `install`, `test`, `lint`
- [x] **Git hooks** (`.githooks/`, turned on by `make hooks`): ruff before commit, tests before push

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
- `idempotency_keys` and `tool_audit` are created now but stay empty until phase 7, so the schema does not change under data later.

## Memory traps file

`data/memory_traps.yaml` is the ground-truth manifest. Each entry has an id, the turns to say, the gap between sessions, the probe question and the expected behavior. Phase 5 builds the eval set from it so data and tests stay in sync.

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
make up          # starts Postgres on localhost:5432
make install     # creates backend/.venv and installs dependencies
make migrate     # applies Alembic migrations
make test
make api
curl localhost:8000/health
```

## Decisions to settle before starting

| Decision | Default if you say nothing |
| --- | --- |
| Project name and repo name | `personal-agent` |
| Chat surface | Web app in phase 4, after the API and agent evals; terminal client in phase 10 |
| Layout | `backend/`, `evals/`, `data/`, `docs/` |
| Database port | 5432, with `DB_PORT` in `.env` to override |
| Tooling | pip, ruff, pytest, a Makefile and git hooks |

## Gate check

| Check | Result |
| --- | --- |
| Schema loads on an empty volume | Six tables created by `alembic upgrade head` (`make db-reset`); `vector` extension present; `alembic check` finds no drift and downgrade then upgrade works |
| `GET /health` | Returns `{"status": "ok"}` against the fresh database |
| Traps file | Loads and validates; eight entries |
| Eval runner | `make eval-memory` loads the empty dataset and exits cleanly (`dev: 0 cases, 8 traps defined`) |
| `pytest` | 26 tests pass: config, health, models and constraints, traps file, case loader, runner |
| Git hooks | `make hooks` turns them on; the pre-commit hook blocks an unformatted file, the pre-push hook runs ruff and the tests |

## Decisions and incidents

- **Port.** The default is 5432, but another local container (`film-rating-db-1`) already used it, so the local `.env` sets `DB_PORT=5434` and the matching `DATABASE_URL`.
- **No owner column.** There is one owner, so sessions, memories and idempotency keys carry no owner id.
- **Traps in YAML, cases in JSONL.** The eight traps are a small hand-written spec; the eval cases are bulk data read line by line.

## Not in this phase

- Embedding, search or any memory write
- Agent loop, sessions and auth
- Chat surface
- MCP client or servers
- Seed data beyond the traps file
