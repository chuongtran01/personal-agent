# Phase 2: Memory store and retrieval

**Status:** done
**Gate:** recall@5 baseline set, and no inactive memory ever returned

## Goal

Make memory retrieval work and make it measurable before any agent uses it. By the end, a seeded set of memories can be searched by keyword, by vector and by both fused, with a recency term, and a retrieval eval reports recall@5 and MRR for each. It also reports a leak count: how often a superseded, deleted, expired or external memory comes back when it must not.

Nothing here writes memories from conversation; that is phase 4. This phase reads from a hand-written set, so the read path can be judged on its own and a later failure can be pinned on the write path or the read path.

The evals are the spec: the retrieval dataset is written before the search code it measures.

## Deliverables

- [x] **Seed memories**: `data/seed_memories.yaml`. 42 hand-written memories for one fake owner, across all four kinds. Some are deliberately not retrievable: superseded rows with a replacement, deleted rows, events that already happened, and `external` rows
- [x] **Embedder**: `backend/app/memory/embeddings.py`. A small `Embedder` interface with an OpenAI implementation (`text-embedding-3-small`, batched) and a fake one for tests, so nothing but the real eval needs an API key
- [x] **Seed loader**: `backend/app/memory/seed.py` (`make seed`). Loads the YAML into `memories` with embeddings and writes a `created` row per memory in `memory_events`, plus a `superseded` or `deleted` row where it applies; a second run writes nothing
- [x] **Search**: `backend/app/memory/search.py`. Keyword, vector and hybrid search behind one `search()` function, with reciprocal rank fusion and a recency term; reads only usable memories
- [x] **Retrieval eval dataset**: `evals/datasets/memory_retrieval.dev.jsonl` and `memory_retrieval.test.jsonl`. Questions with the memories that should come back and the ones that must not
- [x] **Retrieval eval runner**: `evals/memory_retrieval_eval.py` (`make eval-memory-retrieval`). Validates the dataset, scores each mode on the chosen split and writes a results file
- [x] **Tests**: `backend/tests/`. Rank fusion, recency, the usable-memory filter on a real database, the embedder with a fake client, the seed loader and its idempotence, and the runner's metrics and dataset validation
- [x] **Baseline numbers**: recorded in this doc, dev first, then test once

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
- `valid_from` is null or not after `now`, unless the caller passes `include_upcoming=True`
- `source_kind = 'owner'`, unless the caller passes `include_external=True`; external results are returned with their `source_kind` so the prompt can label them

**Recency.** `recency = exp(-age_days / half_life_days)` where age is measured from `COALESCE(valid_from, created_at)` to `now`. The final score is `rrf + recency_weight * recency`. `now` is a parameter, never `datetime.now()` inside the search, so the eval controls the clock. The weight and half-life are settings; the first values are guesses, tuned on dev only and changed in this doc if they move.

**Result.** Every result carries `memory_id`, `kind`, `content`, `source_kind`, `valid_from`, `valid_until`, `created_at` and its scores, so the agent can show dates and sources in the prompt.

`last_used_at` is not updated by `search()`. The agent loop does that when a memory is actually put in a prompt (phase 3), so an eval run does not change the data it is measuring.

## Retrieval eval

- **Dataset.** Two files, one JSON object per line. Every row has the same ten fields, with an empty value where one does not apply: `id`, `category`, `question`, `relevant_keys` (memories that should come back), `forbidden_keys` (memories that must not, such as the superseded or deleted twin of the right answer), `include_external`, `include_upcoming`, `expect_no_answer`, `trap_id` and `note`. The runner validates each row against one Pydantic model and fails on a missing or unknown field, an unknown memory key, or an id or question that appears in both files.
- **Coverage.** Direct questions, paraphrases that share no words with the memory (where vector search should win), exact-term questions (names, places, where keyword should win), changed-fact questions where the old value is forbidden, forgotten-memory questions, stale-event questions, questions that need two memories, unrelated questions where nothing should come back, and questions where an `external` memory is the only match.
- **Dev and test.** Dev is for tuning the recency weight and half-life and is the only split read by default. Test is held out, read once with `--split test`, and is the headline. Rows written without looking at the memories go in test.
- **Metrics.** Each question retrieves 10 memories.
  - **recall@5**: the share of answerable questions with a relevant memory in the top 5.
  - **MRR**: the mean of 1 / rank of the first relevant memory (0 when none is in the top 10).
  - **all memories @5**: the share of questions where every relevant memory is in the top 5.
  - **leaks**: the number of forbidden memories returned anywhere in the top 10, summed over all questions. The gate is zero, in every mode.
  - **no-answer top score**: for the questions with nothing relevant, the best score per mode, against the best score of answerable questions. This suggests the threshold the agent uses to say "I don't know" in phase 3.
- **Report.** One row per mode plus recall by category, printed and written to `evals/results/` (git-ignored) with every question's ranked results. `make eval-memory-retrieval` runs dev; `ARGS="--split test"` runs the held-out split; `ARGS="--modes keyword"` runs without an API key.

### Negative control

`make eval-memory-retrieval ARGS="--control"` lifts every filter (`include_inactive`, `include_ended`, `include_upcoming`, `include_external`) and reports how many questions with forbidden memories get one back in the top 10. It is a check on the leak gate, not a score: a zero-leak result only means something if the questions would leak without the filter. It reports and does not gate.

### Baseline

Run on the dev split before any tuning: 33 questions, 25 answerable, so one question is 4 points of recall. Recency settings are the first guesses (weight 0.005, half-life 90 days); the table after the tuning section has the numbers with the tuned settings. The seed set is 42 memories embedded with `text-embedding-3-small`.

| Mode | recall@5 | MRR | all@5 | Leaks |
| --- | --- | --- | --- | --- |
| Keyword | 0.720 | 0.700 | 0.720 | 0 |
| Vector | 0.960 | 0.966 | 0.960 | 0 |
| Hybrid | 0.960 | 0.879 | 0.960 | 0 |
| Hybrid with recency | 0.920 | 0.822 | 0.880 | 0 |

recall@5 by category (dev):

| Category | n | Keyword | Vector | Hybrid | Hybrid with recency |
| --- | --- | --- | --- | --- | --- |
| changed_fact | 3 | 1.00 | 1.00 | 1.00 | 1.00 |
| direct | 5 | 1.00 | 1.00 | 1.00 | 1.00 |
| exact_term | 3 | 1.00 | 1.00 | 1.00 | 1.00 |
| external | 1 | 1.00 | 1.00 | 1.00 | 1.00 |
| messy | 2 | 1.00 | 1.00 | 1.00 | 1.00 |
| multi | 2 | 0.50 | 1.00 | 1.00 | 1.00 |
| near_duplicate | 1 | 1.00 | 1.00 | 1.00 | 1.00 |
| paraphrase | 5 | 0.20 | 0.80 | 0.80 | 0.60 |
| stale_event | 2 | 0.50 | 1.00 | 1.00 | 1.00 |
| upcoming | 1 | 0.00 | 1.00 | 1.00 | 1.00 |

Negative control on dev: forbidden memories came back for 8 of 12 questions in keyword mode and 12 of 12 in vector and hybrid; hybrid with recency returned one fewer (11 of 12, never for the health question). The four keyword misses (`m019` Bonfim, `m026` health, `m035` exercise, `m050` "coffe after 3?") share no words with their forbidden memory, so keyword cannot reach them. For vector and hybrid every forbidden-memory question is a real trap, so zero leaks there means the filter is doing the work.

Top score, median (min to max): vector scores answerable questions 0.465 (0.258 to 0.745) and no-answer questions 0.176 (0.078 to 0.342). The ranges overlap between 0.26 and 0.34, so one "I don't know" threshold would be imperfect; phase 3 decides how to use it.

What the baseline shows:

- Vector is the strongest single mode. Hybrid matches its recall but has a lower MRR (0.879 against 0.966): keyword results pull the right memory down. Keyword is weakest, mostly on paraphrases.
- At the guessed settings recency makes things worse (recall 0.92, MRR 0.822). It lifts recently created, unrelated memories (`dentist-future`, `dinner-ines`, `water-3`) above the right ones, and pushed the dog memory to rank 7 for the New Year's Eve question.
- One miss in every embedding mode: "Can you suggest a restaurant for dinner tonight?" finds `diet` at rank 7, behind the dinner and run memories.

### Tuning the recency settings

Dev only, as planned; the test split is not read until the settings are fixed. The rule is set before the sweep so the result cannot be argued into place:

- Sweep `recency_weight` over 0, 0.0005, 0.001, 0.002 and 0.005, and `recency_half_life_days` over 30, 90 and 365, on hybrid with recency. Weight 0 is plain hybrid.
- Pick the setting with the highest MRR among those whose recall@5 is not below plain hybrid's. Ties go to the smaller weight.
- If the best setting is weight 0, recency does not help on this data: the default weight becomes 0 and the mode stays available but is reported as not better than hybrid.
- A sweep refuses the test split.

Result of the sweep on dev (hybrid with recency; recall@5 was 0.960 in every row except the three marked):

| Weight | Half-life (days) | recall@5 | MRR |
| --- | --- | --- | --- |
| 0 (plain hybrid) | n/a | 0.960 | 0.879 |
| 0.0005 | 30, 90 or 365 | 0.960 | 0.899 |
| 0.001 | 30 / 90 / 365 | 0.960 | 0.872 / 0.872 / 0.868 |
| 0.002 | 30 / 90 / 365 | 0.960 | 0.872 / 0.846 / 0.868 |
| 0.005 | 30 | 0.960 | 0.832 |
| 0.005 | 90 | 0.920 | 0.822 |
| 0.005 | 365 | 0.920 | 0.811 |

By the rule, the setting is **weight 0.0005 and half-life 365 days** (the three half-lives tie at weight 0.0005, so the longest wins). These are now the defaults in `config.py` and `.env.example`. Two cautions: the gain over plain hybrid is +0.020 MRR, which on 25 questions is about one answer moving up one place, so it is within noise; and larger weights are clearly worse, which is the finding that matters. Recency stays a small tie-breaker.

Dev with the tuned settings (the numbers the sweep chose on, so they are not an independent result):

| Mode | recall@5 | MRR | all@5 | Leaks |
| --- | --- | --- | --- | --- |
| Keyword | 0.720 | 0.700 | 0.720 | 0 |
| Vector | 0.960 | 0.966 | 0.960 | 0 |
| Hybrid | 0.960 | 0.879 | 0.960 | 0 |
| Hybrid with recency | 0.960 | 0.899 | 0.960 | 0 |

### Test split

Read once, after the settings were fixed, and nothing was changed afterwards. 17 questions, 14 answerable, so one question is 7 points of recall. As noted above, these questions were written while looking at the seed memories and then split by position, so this split is a sanity check, not an independent measurement.

| Mode | recall@5 | MRR | all@5 | Leaks |
| --- | --- | --- | --- | --- |
| Keyword | 0.714 | 0.625 | 0.643 | 0 |
| Vector | 1.000 | 1.000 | 1.000 | 0 |
| Hybrid | 1.000 | 0.881 | 0.929 | 0 |
| Hybrid with recency | 1.000 | 0.917 | 0.929 | 0 |

recall@5 by category (test): vector, hybrid and hybrid with recency score 1.00 in every category. Keyword scores 1.00 on direct, exact-term, external, multi-memory, near-duplicate and upcoming questions, 0.50 on changed facts, and 0.00 on the messy and paraphrase questions. Top scores for vector: answerable 0.395 (0.302 to 0.694), no-answer 0.121 (0.088 to 0.259); these ranges do not overlap on test, but they do on dev.

### What the baseline says

- **Vector alone is the best mode on both splits.** It has the highest MRR on dev (0.966) and test (1.000). Hybrid ties it on recall and loses on MRR on both. On this small, paraphrase-heavy set, adding keyword search does not help; it only matters for exact terms, where both already score 1.00.
- **Recency helps hybrid slightly at best and hurts at the guessed settings.** It never beats vector.
- **Zero leaks in every mode on both splits**, and the negative control shows the questions would leak without the filter, so the gate is a real check.
- **Open for phase 3:** the default search mode. The numbers point to vector, or hybrid if exact names matter more in real use than they do in this data. The set is too small to settle it, so this is a decision for phase 3, not a result.

## Run it

```sh
make up
make migrate
make seed                    # needs OPENAI_API_KEY for the embeddings
make eval-memory-retrieval   # dev split
```

## Decisions made

| Decision | Outcome |
| --- | --- |
| Where the seed memories live | `data/seed_memories.yaml`, loaded by `make seed`; the eval reads the same file through the same loader (`SeedMemory`), so data and tests cannot drift |
| Recency in the first baseline | Reported with and without it. At the guessed settings it hurt; tuned on dev to weight 0.0005, half-life 365 days, a small tie-breaker |
| Facts that have not started | Hidden by default and returned with `include_upcoming=True`. Events that are still ahead need no flag: only a fact with a future `valid_from` is hidden |
| External memories | Excluded by default; included only with `include_external=True` and always returned with `source_kind` |
| Seed identity | A seed memory is matched by its content, so the `memories` table has no key column. After editing the wording of a seed memory, run `make seed ARGS=--reset` |
| Eval database | The runner uses its own `agent_eval` database, emptied and re-seeded on every run, never the dev database |
| Default search mode | Left open for phase 3; vector alone scored best on both splits, but the set is small |

## Gate check

| Check | Result |
| --- | --- |
| Seed loads | 42 memories, 42 `created` events and 8 `superseded` or `deleted` events; a second run adds nothing and calls no embedding API |
| Leaks | Zero in all four modes, on dev (33 questions) and on test (17 questions) |
| Negative control | Forbidden memories come back when the filters are lifted: 12 of 12 questions in vector and hybrid on dev, 8 of 12 in keyword (the other four share no words with their forbidden memory) |
| Baseline | Recorded for keyword, vector, hybrid and hybrid with recency, on dev and on test, above |
| `pytest` | 129 tests pass without an API key |

Limits to keep in mind: the dev split has 25 answerable questions and the test split 14, so one question is 4 and 7 points of recall; and the test questions were written with the seed memories in view, so test is a sanity check and not an independent measurement.

## Not in this phase

- Writing memories from conversation (extraction, reconciliation, supersede and forget): phase 4
- Updating `last_used_at`: phase 3, when the loop puts a memory in a prompt
- Any agent, session or chat surface
- A reranker
- Summarising or expiring old memories
