# Phase 2: Memory store and retrieval

**Status:** not started
**Gate:** recall@5 baseline set, and no inactive memory ever returned

## Goal

Make memory retrieval work and make it measurable before any agent uses it. By the end, a seeded set of memories can be searched by keyword, by vector and by both fused, with a recency term, and a retrieval eval reports recall@5 and MRR for each. It also reports a leak count: how often a superseded, deleted, expired or external memory comes back when it must not.

Nothing here writes memories from conversation; that is phase 4. This phase reads from a hand-written set, so the read path can be judged on its own and a later failure can be pinned on the write path or the read path.

The evals are the spec: the retrieval dataset is written before the search code it measures.

## Deliverables

- [x] **Seed memories**: `data/seed_memories.yaml`. 42 hand-written memories for one fake owner, across all four kinds. Some are deliberately not retrievable: superseded rows with a replacement, deleted rows, events that already happened, and `external` rows
- [ ] **Embedder**: `backend/app/memory/embeddings.py`. A small `Embedder` interface with an OpenAI implementation (`text-embedding-3-small`, batched) and a fake one for tests, so nothing but the real eval needs an API key
- [ ] **Seed loader**: `backend/app/memory/seed.py` (`make seed`). Loads the YAML into `memories` with embeddings and writes a `created` row per memory in `memory_events`; a second run writes nothing
- [ ] **Search**: `backend/app/memory/search.py`. Keyword, vector and hybrid search behind one `search()` function, with reciprocal rank fusion and a recency term; reads only usable memories
- [ ] **Retrieval eval dataset**: `evals/datasets/memory_retrieval.dev.jsonl` and `memory_retrieval.test.jsonl`. Questions with the memories that should come back and the ones that must not
- [ ] **Retrieval eval runner**: `evals/memory_retrieval_eval.py` (`make eval-memory-retrieval`). Validates the dataset, scores each mode on the chosen split and writes a results file
- [ ] **Tests**: `backend/tests/`. Rank fusion, recency, the usable-memory filter on a real database, the embedder with a fake client, the seed loader and its idempotence, and the runner's metrics and dataset validation
- [ ] **Baseline numbers**: recorded in this doc, dev first, then test once

## Seed memories

One fake owner with a made-up life, so the data is safe to commit and to send to a model. The set is written by hand and each trap-shaped memory points at the trap it supports in `data/memory_traps.yaml`.

| Group | Count (about) | Why it is there |
| --- | --- | --- |
| Ordinary active memories | 20 | Facts, preferences and episodes the owner would plausibly state: home, work, diet, habits, people, routines |
| Superseded | 5 | Old rows (`superseded`, `superseded_by` set) whose replacements are among the ordinary memories. Retrieval must return the new one and never the old one |
| Deleted | 3 | `status = deleted`. Must never come back, whatever the question |
| Past events | 4 | `valid_until` before the eval clock. Must not be offered as upcoming |
| Future events and later facts | 3 | Two events with `valid_until` ahead of the clock, which are retrievable, and one fact with `valid_from` ahead of the clock (a job that starts in January), which is hidden until the caller asks for upcoming facts |
| `external` | 4 | From an email or event; one carries a hidden instruction. Retrievable only when the call allows it, and always labelled |
| Near-duplicates | 3 | The same fact in different words, to see whether search ranks them together |

Each entry has `key`, `kind`, `content`, `status`, `source_kind`, `valid_from`, `valid_until`, `created_at`, `superseded_by` (a `key`) and a short `note` on what it is testing. The loader turns `key`s into ids.

The eval runs against a fixed clock (`EVAL_NOW` in the dataset module), so "already happened" does not change as the real date moves.

## Embedder

`Embedder` has one method, `embed(texts) -> list[list[float]]`, so the search and the seed loader take it as an argument and tests pass in a fake that returns fixed vectors. The OpenAI version batches 100 texts per call and checks that each vector has `embedding_dimensions` entries, so a wrong model fails loudly and not as a silent database error.

## Search

| Mode | How it works |
| --- | --- |
| Keyword | `websearch_to_tsquery` against `memories.tsv` with the ANDs turned into ORs, ranked with `ts_rank_cd` |
| Vector | Cosine distance on `memories.embedding` using the HNSW index |
| Hybrid | Reciprocal rank fusion of the keyword and vector rankings |
| Hybrid with recency | Hybrid score plus a recency term (below) |

**Which memories are searched.** Every mode applies the same filter, in SQL and not in Python, so a leak is impossible by construction and the leak count in the eval is a check of that, not a hope:

- `status = 'active'`
- `valid_until` is null or after `now`
- `valid_from` is null or before `now`, unless the caller asks for upcoming events
- `source_kind = 'owner'`, unless the caller passes `include_external=True`; external results are returned with their `source_kind` so the prompt can label them

**Recency.** `recency = exp(-age_days / half_life_days)` where age is measured from `COALESCE(valid_from, created_at)` to `now`. The final score is `rrf + recency_weight * recency`. `now` is a parameter, never `datetime.now()` inside the search, so the eval controls the clock. The weight and half-life are settings; the first values are guesses, tuned on dev only and changed in this doc if they move.

**Result.** Every result carries `memory_id`, `kind`, `content`, `source_kind`, `valid_from`, `valid_until`, `created_at` and its scores, so the agent can show dates and sources in the prompt.

`last_used_at` is not updated by `search()`. The agent loop does that when a memory is actually put in a prompt (phase 3), so an eval run does not change the data it is measuring.

## Retrieval eval

- **Dataset.** Two files, one JSON object per line. Every row has: `id`, `category`, `question`, `relevant_keys` (memories that should come back), `forbidden_keys` (memories that must not, such as the superseded or deleted twin of the right answer), `include_external` (false by default), `expect_no_answer` (false by default), `trap_id` and `note`. The runner validates each row against one Pydantic model and fails on a missing or unknown field, an unknown memory key, or an id or question that appears in both files.
- **Coverage.** Direct questions, paraphrases that share no words with the memory (where vector search should win), exact-term questions (names, places, where keyword should win), changed-fact questions where the old value is forbidden, forgotten-memory questions, stale-event questions, questions that need two memories, unrelated questions where nothing should come back, and questions where an `external` memory is the only match.
- **Dev and test.** Dev is for tuning the recency weight and half-life and is the only split read by default. Test is held out, read once with `--split test`, and is the headline. Rows written without looking at the memories go in test.
- **Metrics.** Each question retrieves 10 memories.
  - **recall@5**: the share of answerable questions with a relevant memory in the top 5.
  - **MRR**: the mean of 1 / rank of the first relevant memory (0 when none is in the top 10).
  - **all memories @5**: the share of questions where every relevant memory is in the top 5.
  - **leaks**: the number of forbidden memories returned anywhere in the top 10, summed over all questions. The gate is zero, in every mode.
  - **no-answer top score**: for the questions with nothing relevant, the best score per mode, against the best score of answerable questions. This suggests the threshold the agent uses to say "I don't know" in phase 3.
- **Report.** One row per mode plus recall by category, printed and written to `evals/results/` (git-ignored) with every question's ranked results. `make eval-memory-retrieval` runs dev; `ARGS="--split test"` runs the held-out split; `ARGS="--modes keyword"` runs without an API key.

## Run it

```sh
make up
make migrate
make seed                    # needs OPENAI_API_KEY for the embeddings
make eval-memory-retrieval   # dev split
```

## Decisions to settle before starting

| Decision | Default if you say nothing |
| --- | --- |
| Where the seed memories live | A YAML file loaded by `make seed`, and the same loader used by the eval, so tests and evals read one set |
| Recency in the first baseline | Report hybrid with and without it, so the effect is visible |
| Upcoming events | Retrievable with a flag; the default read path hides events that have not started yet only if the caller says so |
| External memories | Excluded by default; included only with `include_external=True` and always returned with `source_kind` |
| Embedding the whole seed set | 42 memories, a fraction of a cent |

## Gate check

| Check | Expected result |
| --- | --- |
| Seed loads | 42 memories and as many `created` events; a second run changes nothing |
| Leaks | Zero forbidden memories returned, in all four modes, on dev and on test |
| Baseline | recall@5 and MRR recorded for keyword, vector, hybrid and hybrid with recency, on dev and on test |
| `pytest` | All tests pass without an API key |

## Not in this phase

- Writing memories from conversation (extraction, reconciliation, supersede and forget): phase 4
- Updating `last_used_at`: phase 3, when the loop puts a memory in a prompt
- Any agent, session or chat surface
- A reranker
- Summarising or expiring old memories
