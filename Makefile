.PHONY: up down db-reset migrate seed install hooks test lint eval-memory eval-memory-retrieval

up:
	docker compose up -d db

down:
	docker compose down

db-reset:
	docker compose down -v && docker compose up -d --wait db && $(MAKE) migrate

migrate:
	cd backend && .venv/bin/alembic upgrade head

# Load the seed memories (needs OPENAI_API_KEY); ARGS=--reset empties the memory tables first
seed:
	cd backend && .venv/bin/python -m app.memory.seed $(ARGS)

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Git hooks: quick checks before a commit, the tests before a push (see .githooks/).
hooks:
	git config core.hooksPath .githooks

test:
	cd backend && .venv/bin/pytest

lint:
	cd backend && .venv/bin/ruff check --config pyproject.toml . ../evals

eval-memory:
	cd backend && .venv/bin/python ../evals/memory_eval.py $(ARGS)

# Retrieval eval on the seeded memories, in its own database. ARGS="--split test" for the held-out split
eval-memory-retrieval:
	cd backend && .venv/bin/python ../evals/memory_retrieval_eval.py $(ARGS)
