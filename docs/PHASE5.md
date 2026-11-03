# Phase 5: Memory writes

**Status:** done
**Gate:** a fact the owner states in one session is used in the next; a changed fact replaces the old one, a forgotten one is never used again, and nothing that should not be stored is stored

## Goal

Until now memory was read-only: the seed file filled it and the agent only recalled. This phase lets the agent learn. After each turn a separate step reads what the owner said and decides what is worth keeping, compares it with what is already stored, and writes the change. The owner's controls are deliberately few: delete a memory, and see why one exists. There is no edit and no restore; to correct or bring back a memory the owner tells the assistant, which goes through the filter and reconciliation like every other write (see "Edit and restore were built and then removed").

Writing is where memory goes wrong in ways reading cannot: it stores things it should not (a card number, a passing remark, text from an email), it stores the same fact five times, it keeps the old city next to the new one, and it deletes the wrong memory when told to forget. So the work is ordered evals first. The extraction eval and the trap cases are written before the extractor, and a few of their checks are zero-tolerance gates, not numbers to improve.

Two pieces of earlier phases are completed here. Phase 3 seeds the memory once per eval run, which is only safe while no case can write; from now on every agent-eval case starts from the same memory. And phase 4's memory list gets its first controls.

## Deliverables

- [x] **Memory write eval and cases first**: `evals/memory_write_cases.py`, `evals/memory_write_checks.py`, `evals/memory_write_eval.py`, `evals/datasets/memory_write.{dev,test}.jsonl`. Fixed turns, each with the memory changes it must and must not produce, checked by code. Written, and run against a stub that stores nothing (the baseline), before the extractor exists
- [x] **Extractor**: `backend/app/memory/extract.py`. One model call after a turn: the owner's messages in, proposed changes out (`add`, `delete`) as structured output, validated before anything uses them
- [x] **Reconciliation**: `backend/app/memory/reconcile.py`. Each proposal is compared with the closest active memories; the result is `noop`, `add`, `supersede(id)` or `delete(id)`, and the rules that keep it safe are code, not prompt
- [x] **Write functions with events**: `backend/app/memory/write.py`. `add_memory`, `supersede` and `delete`, each with its `memory_events` row and the reason, so any change can be explained, and `apply_decisions` for a whole turn
- [x] **Filter before write**: `backend/app/memory/filter.py`. Card numbers, passwords, PINs and long identifiers are dropped in code whatever the model proposed
- [x] **Agent integration**: the loop starts extraction after the reply is stored and `done` is sent; a failure there never touches the reply; a constructor option turns it off for the memory-off eval
- [x] **Memory API**: `DELETE /memories/{id}` and `GET /memories/{id}/events`
- [x] **Memory list controls**: delete in the web app, and the history of one memory (when it was written, from which message, what replaced it)
- [x] **Eval isolation**: the agent eval restores the seeded memories before every case, without calling the embeddings API again
- [x] **Memory eval**: `evals/memory_eval.py` plays the trap cases (sessions, gaps, probe) against the real agent, memory on and off, dev three times and test once. Dev and the held-out test split are both measured; see "Held-out test results"
- [x] **Tests**: unit tests with a fake model for extraction, reconciliation, the filter, the write functions, the API and the loop. None needs an API key
- [x] **Docs and baselines**: results recorded here, `DESIGN.md` and the README updated, and the open decisions below settled

## Architecture

```
turn ends  --> reply stored --> AgentDone sent --> (the chat is free for the next message)
                                      |
                                      v
                       extraction task, one at a time
   owner messages of the turn
        |  extract (1 model call)       -> proposals: add / delete, each a plain sentence
        |  filter  (code)               -> sensitive proposals dropped
        |  reconcile (code + 1 call)    -> nearest active memories decide: noop / add / supersede / delete
        |  write   (one transaction)    -> memories row + memory_events row, with the reason
        v
   the next recall sees it

owner deletes in the web app --> DELETE --> the same write function
```

Extraction runs after the reply, so it adds no latency to the answer. The cost is that it can still be running when the owner sends the next message. Extractions go through one queue and run in order, so a second one reconciles against what the first wrote, and a recall that races a write sees the memory one message late, which is acceptable. The queue lives in the one worker process, like the turn lock (see `PHASE3.md`).

### Extraction

The input is the owner's messages of the turn, with the last few earlier messages as context. The assistant's reply is shown as context, labelled as the assistant's, and is never a source: a fact the model said and the owner did not is not stored. Tool results are never in the input (there are none yet; phase 6 keeps them out, and anything from an outside source can only be stored as `external`).

The call returns a list of proposals, each one `{op, kind, content, valid_until}`:

- `add`: one self-contained sentence in the third person ("The owner has a dentist appointment on 3 March 2027"), with a kind (`fact`, `preference`, `event`, `episode`). Only an event has a date, `valid_until`, the last day it takes place. There is no `valid_from`: recall hides a memory whose `valid_from` is still ahead of the clock (phase 2), so a start date would make next week's dinner invisible until the day. The call is given today's date and the owner's time zone, so "this Friday" becomes a date
- `delete`: a sentence describing what the owner asked to forget, not an id; finding the memory is reconciliation's job

Questions, greetings, small talk, requests ("book me a table"), and what the assistant said produce nothing. The empty list is the usual answer.

### Reconciliation

For each proposal the nearest active memories are found by vector search. Unlike recall this does not hide ended events, facts that start later or an email's memory: an ended event must still be found to be forgotten, and a repeat is a repeat whatever its dates. What it leaves out is what is already gone, replaced and deleted memories, so those are never candidates. Then:

| Case | What happens |
| --- | --- |
| No candidate closer than the threshold | `add`, with no second model call |
| Candidates exist | One call with the proposal and up to five candidates, each with its id and date; it answers `same` (noop), `replaces` plus an id (supersede), `new` (add), or for a delete the ids that match |
| The model names an id that was not shown | The proposal is dropped, a delete too (one stray id makes the whole answer untrustworthy); ids are checked in code |
| A delete matches nothing | Nothing is deleted, and nothing is stored in its place |
| A delete matches several | The ones the model names, all of which must have been shown; at most five deleted per turn |

Supersede writes a new row, sets the old one `superseded` with `superseded_by`, and sets the old row's `valid_until` to the time of the message, so history questions still work. Nothing is overwritten and nothing is hard-deleted.

Because the call that reconciles sees the candidates' ids, this is the one place memory ids go to a model. The reply's prompt still never contains them.

### Filter

Dropped in code, after extraction and before reconciliation: card numbers (a digit run that passes the Luhn check), IBAN-like strings, anything near the words password, passcode, PIN or secret, and long digit or character runs that look like an identifier or key. A dropped proposal is logged as a count, never with its text, so the log does not become the store. The extraction prompt also says not to propose these; the filter is the part that is tested as a guarantee.

### What a write records

`memory_events` already allows `created`, `superseded`, `deleted` and `restored`; this phase writes the first three, since there is no restore. Each write adds a row with the memory id, the event, a reason in words ("the owner said it", "the owner's new statement replaced it", "the owner asked to forget it") and the message id it came from. A proposal that ends as a no-op writes nothing and so has no event. `source_message_id` on the memory is the owner's message and `source_kind` is `owner`. This phase never writes `external`.

The owner's own change in the app is delete only. A deleted memory is final: it is never recalled again, and the row and its event stay so the change can be explained. Recalled memories shown under old replies resolve to "since replaced" or "deleted".

## Memory write eval, as built

`make eval-memory-write` (`ARGS="--split test"`, `"--repeat 3"`, `"--case e021"`). It tests the write path (extractor, then reconciliation, then the write), which is why it is not called the extraction eval. The pieces:

- `evals/memory_write_cases.py`: the row schema and loader. Every field is required, rows are checked for consistency on load (a change names an active memory, a case checks something, no word is both required and forbidden), and the two splits may not share an input.
- `evals/memory_write_checks.py`: the checks, with no model and no database. The step under test answers with `Add`, `Supersede(target)` and `Delete(target)`, naming existing memories by a case key; `check_case` returns the failures, plus two zero-tolerance lists apart from the score: `stored` (a forbidden word, or a card-like number found by the check itself, in a new memory) and `wrong_changes` (a delete or replacement the owner did not mean, or of a memory that is not active or was not shown).
- `evals/memory_write_eval.py`: the runner. A writer takes a case and the fixed clock and returns an `Outcome` (changes and tokens). `--writer none` is the baseline that stores nothing; `--writer extract` runs the extractor alone; `--writer pipeline` adds reconciliation. A writer that raises fails only its case. Runs go through `--concurrency` model calls at once (4 by default). The gate is zero `stored` and zero `wrong_changes`, exit status 1 otherwise. Results go to `evals/results/`.
- `evals/datasets/memory_write.dev.jsonl` (37 cases) and `.test.jsonl` (16): eleven categories, all in both splits. Each trap in `data/memory_traps.yaml` has a case. The clock is Wednesday 28 October 2026 in Lisbon, so "this Friday" is 30 October and "3 March" is 3 March 2027.

Decisions made while building it:

- A write is judged on words, kind and dates, never on exact wording, so a model that writes "The owner is allergic to penicillin" and one that writes "Penicillin allergy" both pass.
- Any write that was not asked for fails the case. That is what makes five wordings of one fact (`e021`) mean one memory, and it is stricter than a recall score: a model that stores a harmless extra fact fails that case.
- An event's end counts if it is the last day or the start of the next day. The start of an event is not checked, because the extractor does not store one; a new memory whose `valid_from` is ahead of the clock fails the case (`hidden_from_recall`).
- A case needs reconciliation when it has existing memories: a right result then depends on memories the extractor never sees (a duplicate, a replacement, a delete, or a "forget" that must leave the others alone). This is derived from `existing`, not a stored flag, so it cannot disagree with it, and the report counts those cases apart from the ones the extractor can pass alone. `--writer extract` runs the extractor only, so it cannot be right on most of them until reconciliation is added to that writer.
- "Forget that" with nothing to match, a request, a tentative remark ("maybe I should learn Spanish") and anything the assistant said are all `no_changes` cases.

**Baseline (writer `none`, nothing stored):** dev 15 of 37, test 8 of 16. These are not passes in any useful sense: the stub does nothing, and a case whose right answer is "change nothing" is passed by doing nothing. They are exactly the no-change cases (no_write, assistant_claim, delete_unclear, and the duplicate and sensitive cases that expect no change). Every case that asks for a write or a delete fails, and "zero stored, zero wrong changes" is trivially true for a writer that writes nothing. The numbers are the floor the extractor must beat on the write cases, and a reminder that the no-change cases alone cannot tell a good extractor from a silent one.

## Extractor, as built

`backend/app/memory/extract.py`, behind `StructuredLLM` (`app/agent/llm.py`), a second small view of the model next to `LLMClient`: one call that answers with parsed JSON. `OpenAIClient.generate` implements it with a strict JSON schema and no stored copy; a call that does not complete, or an answer that is not JSON, raises `LLMError` without a retry (the loop retries chat calls; a missed memory is a quality problem and nothing here retries it). The model is `EXTRACTION_MODEL`, which defaults to the chat model.

- **Input:** the owner's messages of the turn (never clipped), up to six earlier messages and the assistant's reply, each in its own tagged section with angle brackets escaped so a message cannot close a section. The instructions say the earlier messages and the reply are for understanding only, and everything inside the tags is data.
- **Output:** `add` (kind, one sentence, `valid_until` for events) and `delete` (a description of what to forget, no id). The model never sees existing memories, so it cannot tell a repeat or a changed fact from a new one; reconciliation does.
- **Checked, not trusted:** a proposal with an unknown op or kind, an empty or over-long sentence (500 characters) is dropped and counted, not repaired. A date on anything but an event is ignored; one that does not parse is ignored and the memory kept. At most five adds and three deletes are kept, and an invalid proposal does not use up a slot.
- **Tests:** 46 for the extractor and 10 for `generate`, with scripted fakes and no key. Nine deliberate breaks (no caps, a date kept on any kind, no escaping, no kind check, an end that is not the next day, a time zone overridden, no context cap, today read in UTC, no length limit) each failed a test.

**Two things found by building it.** The dates: the first eval cases put an event's start in `valid_from`, which phase 2's recall would have hidden until that day, so the cases and checks were changed to the end date only. And `e009` ("It was our wedding anniversary yesterday") was ambiguous: the model stored "The owner's wedding anniversary is on 27 October" as a fact, three runs out of three, which is reasonable because an anniversary recurs. The case was revised after the first run to check that "yesterday" became 27 October, which is what it was for. That is a change to a dev case after seeing a result, recorded here for that reason.

**First results, extractor alone (`--writer extract`), dev, three runs, 111 cases played:** 84 passed, which reads as two groups. The 26 cases with no existing memories (78 runs) passed in all three runs: 18 of 18 adds, 9 of 9 assistant claims, 9 of 9 event dates, 6 of 6 multi-fact, 18 of 18 no-write, 6 of 6 non-English, 9 of 9 sensitive, and the five wordings of one fact (`e021`) written once. The 11 cases with existing memories (33 runs) passed 6, and only because this writer ignores delete proposals: they are the two "forget" requests with nothing to match (`e029`, `e030`). The other nine fail by design, three times each, because they need reconciliation: two duplicates (`e019`, `e020`: it stores the repeat, which it cannot see), four supersedes (it adds the new fact, which is right, but cannot replace the old one) and three deletes (it cannot find a memory). Zero forbidden words stored, zero wrong changes. About 670 input and 80 output tokens a turn, most of it the instructions. The sensitive cases passed on the model alone, with no filter yet; the filter is still the guarantee. The test split has not been run: it is read once, with the whole pipeline.

One thing it showed for reconciliation: for "Actually, I hate mornings. I run in the evening." the extractor wrote two preferences ("dislikes mornings", "runs in the evening"), or sometimes one. How reconciliation handled that is under "Reconciliation, as built".

## Reconciliation, as built

`backend/app/memory/reconcile.py` and `neighbours.py`. `reconcile(llm, neighbours, proposals, owner=...)` returns decisions and writes nothing, so a failure part-way leaves memory untouched; the write functions apply the decisions with their events.

- **Lookup:** `Neighbours.nearest(text, limit)` is a one-method protocol. `DbNeighbours` is the Postgres version (cosine distance over active rows with an embedding, any validity and source); the eval has an in-memory one over the memories written in a case. Candidates below `RECONCILE_MIN_SIMILARITY` (0.35) are not asked about, so a statement unlike anything stored is added with no model call.
- **Decision call:** for an add, `same` (a stored memory already says it: nothing is written), `replaces` (it makes a stored memory untrue: supersede, with every memory named) or `new`; for a delete, the ids the owner meant. Up to five candidates are shown with id, kind, source and date, and the model is told to prefer `new` when unsure: a stored near-duplicate is harmless and a lost fact is not. The similarity is not shown, so it does not anchor the answer.
- **The owner's own words go with it.** The proposals are the extractor's rewording, which loses "actually", "I switched" and the like, the strongest sign of a replacement. The first pipeline run missed `e023` because the reconciler saw only "The owner runs in the evening." beside "likes running in the morning" and, told to prefer `new`, kept both.
- **Checked in code:** every id must be one that was shown (a stray id drops the proposal, counted, and not repaired); `same` or `replaces` with no id is dropped; at most five memories deleted per turn; a memory replaced or deleted by an earlier decision of the same turn is not offered to the next, so two proposals cannot replace one memory twice. Reasons for `memory_events` are fixed phrases that never quote a memory.
- **Tests:** 49 for `reconcile`, 7 for `DbNeighbours` (against the test database) and 14 for the pipeline writer. Twenty deliberate breaks across the three (a strict floor, stray ids accepted, a `True` read as id 1, the cap, the already-replaced filter, an unescaped memory, a reason that quotes a memory, wrong order, no status filter, and others) each failed a test, after three of them first survived and the tests were tightened.

**What the similarity numbers say.** Real matches in the dev cases scored from 0.48 (a sister's allergy, correctly deleted when asked to forget the sister) to 1.00; unrelated neighbours scored 0.35 to 0.52. The two ranges overlap, so no floor separates them and the model has to decide. 0.35 is kept as a loose floor below the lowest true match, only to skip the call for something unlike everything stored. With a store this small (two or three memories per case) the five-candidate limit is not doing any filtering yet; a larger store is where the floor and the limit start to matter, and phase 9 should look again.

**Eval changes made for it.** A write can be `optional` (`e023` is a message with two facts: the morning preference must be replaced, by either memory, and the evening run may be a second one; three runs produced one merged memory, or a replacement plus an addition, and all are right). `e023` was revised after the first pipeline run for that reason. Changes that name a memory beyond the first (`Supersede.also`) are judged on each target.

**Results, the pipeline (`--writer pipeline`), dev, three runs, 111 cases played:** 111 passed. The 26 cases with no stored memory: 78 of 78. The 11 with stored memories: 33 of 33 (two duplicates, four supersedes, three deletes and the two "forget" requests with nothing to match, which now pass because reconciliation really declines). Zero forbidden words stored, zero wrong changes. About 760 input and 90 output tokens a turn across both steps, against about 670 and 85 for the extractor alone: reconciliation adds roughly a tenth, because most turns store nothing or find nothing close. The test split has not been run: it is read once, when the whole write path (filter, write functions and the loop) is in. All of this is on the dev split, where the prompts and two cases were adjusted, so 111 of 111 is the number the test split has to confirm, not a result of its own.

## Write functions, as built

`backend/app/memory/write.py`. Three functions, `add_memory`, `supersede` and `delete`, each writing its change and its `memory_events` row, and `apply_decisions`, which applies what reconciliation decided.

- **The caller owns the transaction.** The three functions flush and do not commit, so an API handler makes one change and commits, and `apply_decisions` applies a whole turn and commits once.
- **Locked and checked before anything is written.** Rows are read `FOR UPDATE`, in id order so two writers cannot deadlock, and every status is checked before the first insert, so a refused change leaves nothing behind (a test checks this without rolling back first). A test with two sessions shows a change waiting for a concurrent one and then seeing its result: the owner deleting a memory while extraction replaces it ends in `WrongStatus`, not in a replaced deleted memory.
- **Status moves.** `active` to `superseded` or `deleted`; `superseded` to `deleted`; `deleted` is final. Deleting what is already deleted changes and logs nothing, so it is safe to repeat.
- **A replacement** adds the new row (with its `created` event) and points each old row at it with `superseded_by`, a `superseded` event each, and an end at the time of the change, or earlier when the old row already had one, so history questions still find it.
- **`apply_decisions`** embeds every new text in one call before writing (if that fails nothing is written), then applies each decision in its own savepoint. A decision whose memory changed since it was made is skipped and counted, and the rest still apply. It returns what it added, replaced and deleted, and how many it skipped.
- **Tests:** 26, against the test database, including recall: a deleted memory leaves the vector search, and a replaced one leaves it and is found as history. Nine deliberate breaks were run while a restore still existed (no row lock, no active check, an end always overwritten, restore always active, a repeated delete logging again, skipped decisions not caught, one embedding call per text, restore from any status, an unknown id ignored) and each failed a test; the two about restore went with it, and the other seven still apply.



## Filter, as built

`backend/app/memory/filter.py`. `filter_proposals` drops every `add` that `sensitivity()` flags, and returns what was kept plus the rule that dropped each one. The pipeline calls it between the extractor and reconciliation, so a dropped secret is never embedded, shown to reconciliation or written; the rest of the turn goes on (the trip is kept, the passport number is not).

| Rule | Catches | Examples |
| --- | --- | --- |
| `card` | a Luhn-valid run of 13 to 19 digits, in fours, in the American Express shape or unbroken | `4111 1111 1111 1111`, `3782 822463 10005` |
| `iban` | two capitals, two digits, then the account | `GB29 NWBK 6016 1331 9268 19`, `PT50 0002 0123 1234 5678 9015 4` |
| `long_number` | any other run of 13 or more digits | `1234 5678 9012 3456`, a 24-digit reference |
| `secret_word` | password, passcode, passphrase, PIN, CVV, OTP, secret, API or private key, access token, security or recovery code | "the wifi password is sunflower" |
| `key` | a token of 20 or more characters with letters and digits, or 32 or more hex digits | `sk-abc123def456ghi789jkl012` |
| `identifier` | a word for an identifier (passport, NIF, social security, driver's licence, ID card) together with a token of six or more digits | "passport number is X1234567", `123-45-6789` |

- **It leans towards dropping, on purpose.** "The owner is bad at remembering passwords" and "likes pin-up art" are dropped too, and a test pins that down. A lost harmless fact costs little; a stored card number costs a lot.
- **A delete is never dropped.** "Forget my card number" must still reach reconciliation and remove a number that slipped in earlier.
- **Phone numbers, dates, times, prices, flight numbers, postcodes and years pass** (a list of 19 ordinary memories is tested). A phone number is contact information, not a credential.
- **Nothing it drops is kept anywhere.** The result and the log name the rule and the count, never the text, and a test reads the log to check.
- **Tests:** 54 for the filter and 3 for the pipeline. Nine deliberate breaks (no Luhn, a broken Luhn, deletes filtered too, no secret-word rule, no key rule, an identifier rule without the word or with the word alone, logging the text, digit runs capped at 19) each failed a test. Writing the tests also found a hole, runs of 20 or more digits slipping past a 13-to-19 pattern, which is fixed.

**What it did in the eval.** On dev, two runs of the whole pipeline (74 cases) passed 74 of 74 with the filter in place, and it dropped nothing: the extractor never proposed a secret in those cases, so here the filter is a backstop that was not tested by the model, only by the unit tests and by scripted extractors that propose a card number or a passport number.

**Limits.** The filter reads a proposal's text only, so a secret spelled out in words ("four one one one…") passes. It is a guarantee against the shapes above, not against every way to write a secret.

## Loop integration, as built

`backend/app/memory/writer.py`, wired in `app/agent/loop.py`, `build.py` and `main.py`.

- **`MemoryWriter`** owns the queue, one worker and the four steps (extract, filter, reconcile, apply). `process(job)` runs one job and returns what happened; `submit(job)` queues it and returns at once; `drain()` waits for the queue (for tests and the eval); `close()` lets queued jobs finish for ten seconds and then stops the worker. The worker starts on the first job and starts again if it ever died.
- **The loop only hands over a job**, after the final reply is stored and the memories are marked used, and before `done` is sent: the owner's message and its stored id, the reply, the chat before this message, and the loop's clock. The chat is free for the next message at once. Nothing is queued when the turn fails (a model error, a recall error, the step limit) or for a reply that only asked for a tool.
- **The context is words only.** The earlier owner messages and the assistant's answers; a tool call, and above all a tool result, never reaches the extractor, so outside text cannot be written as an owner fact. An earlier owner message whose turn failed shows up as context, since it was never extracted.
- **A job that fails is logged and forgotten.** The log names the message id and the kind of error, never the owner's text, and the worker carries on. A full queue (100) drops the new job with a warning. Neither can change a reply that was already sent.
- **Switches.** `MEMORY_WRITES_ENABLED=false` keeps recall and stops learning. `AgentLoop(memory_writer=None)` is how the eval turns writing off, next to `recall_enabled=False`. `EXTRACTION_MODEL` picks the model for both write-path calls and defaults to the chat model. On shutdown the app closes the agent, so queued writes get ten seconds to finish.
- **Tests:** 22 for the writer, 13 for the loop's side of the hand-over and 7 for the server wiring. Twenty-seven deliberate breaks (13 on the writer, 10 on the loop, 4 on the wiring) each failed a test. Two survived at first (the owner's words never reaching reconciliation, and a worker that died on its own) and the loop's context test was too weak to notice intermediate assistant text; all three were fixed. One break (a close that never cancels) hangs, which is how it was caught, and led to running the mutation checks with a timeout and a restore that always happens.

**A real run, once.** The whole path, with the real model and a throwaway database, one chat per message: "I live in Lisbon, in the Alfama neighbourhood" became one memory; "I moved to Porto last week" replaced it (the old one kept as history, ended on the day); a new chat's "Where do I live?" answered "You live in Porto"; "I'm vegetarian" and "I don't eat meat, by the way" made one memory; a card number and PIN made none; and "Please forget that I used to live in Lisbon" deleted the old memory, after which "where I lived before Porto" got "I don't know". That is the gate's first line, and the update, forgetting and secret cases, working on the real thing. It is one run on fake data and not the end-to-end eval, which is still to come.

**What that run found.** The first run did not forget: the Lisbon memory had already been replaced, and reconciliation only looked at active memories, so it could not find it, and the agent went on answering from it. The eval had missed it because every delete case targeted an active memory. A request to forget now also searches the replaced memories, marked "no longer true" for the model, which is told that a request about the past is about those and a request about the present is not. Adds still compare only with active memories, so a repeated old fact is not mistaken for a repeat. Two cases cover it (`e038` and `e117`, one in each split, added before the test split was read), and the eval's lookup and checks follow the same rule: a replaced memory may be deleted, never replaced, and a deleted one neither.

**Resolved later:** the assistant's wording when told to forget is controlled now. See "What the assistant says when told to forget, as built".

## Memory API, as built

`backend/app/api/memories.py`. Behind the owner's login, like the rest. Errors are `{"detail": {"code", "message"}}`, as in the chat API.

| Call | Does |
| --- | --- |
| `GET /memories` | The list, newest first, by status (phase 4, unchanged) |
| `DELETE /memories/{id}` | Status `deleted`, whatever it was (a replaced memory too): it is never recalled again. The text stays on file and is listed under Deleted. Deleting what is deleted is a 204 again, with no second event |
| `GET /memories/{id}/events` | The log for one memory, oldest first: event, reason, message id (null for the owner's own actions), time |

The only error is `memory_not_found` (404) for an unknown id. There is no `PATCH`, no `PUT`, no `POST` and no restore (a test pins that they answer 405 and 404). `get_now` is the clock, a dependency so a test can fix it. The delete reason is a fixed phrase, "deleted by the owner in the app", that never quotes the text.

**Tests:** 12 for the endpoints, against the test database: sign-in on both, delete (the text kept, never recalled again, twice, a replaced memory, an unknown one), the events (their order and reasons, a replacement's, an unknown memory's) and the absence of the removed routes. Six deliberate breaks on what is left (no commit, an unknown id uncaught, a wrong reason, events newest first, no 404 for the events of an unknown memory, a 200 instead of a 204) were caught except the last, which changes nothing a client sees because the handler returns an explicit 204.

## Edit and restore were built and then removed

The API first had `PATCH /memories/{id}` (edit) and `POST /memories/{id}/restore`, with 36 tests, and a real run showed them working. They were removed before any screen used them, for these reasons:

- **They are second ways to do what the agent already does safely.** "Actually I live in Porto" goes through the filter, reconciliation, supersede and the event log; saying a fact again after a mistaken delete does too. An edit or a restore skipped all of that.
- **Restore can put a memory beside one that contradicts it.** Delete "lives in Lisbon", say "I live in Porto", restore Lisbon: two active, contradicting facts, which is the problem the whole design exists to prevent. Warning about it was possible, but it is more machinery (a similarity check, a response field, a screen) for something the agent covers.
- **Edit had loose ends.** It could duplicate or contradict another memory, an event's date could not be edited so a changed date kept a stale end, it needed its own filter check, and its rows had no source message.
- **A restore sits badly with deleting for good.** Restoring something the owner wanted gone is the opposite of a privacy control.

What was lost is direct editing in the app and one-click undo; for one owner, saying it in chat is a small step. What chat cannot do yet is name the exact memory to change or say truthfully what it changed, because the write happens after the reply; phase 12 adds that, through memory tools and the confirmation of phase 7, once phase 6's `search_memory` can find the memory. What it keeps simple is the rule that every write except delete goes through the agent's path, where the filter guards, reconciliation decides and the events explain. The schema still allows a `restored` event, which nothing writes; removing it would need a migration for no gain.

## Web app

The memory list from phase 4 gains, on each Active and Replaced row, a delete control, and a history view per memory (a collapsible under the row, loaded on open: when it was written and from which message, what replaced it, when it was deleted). Delete asks first, in a dialog that names the memory and says what will happen ("It will not be used again."). There is no edit and no restore, and the page says how to correct something: tell the assistant. Controls are keyboard-reachable, and after a delete the list updates in place and the change is announced. Deleted rows stay read-only.

The chat view needs no change: a reply recalled memories by id, and a memory replaced or deleted since already shows as "since replaced" or "deleted" (phase 4).

## Memory list controls, as built

`frontend/src/components/memory-list.tsx`, with `api/memories.ts` (`deleteMemory`, `getMemoryHistory`), `lib/use-memory-history.ts` and `lib/memory-view.ts`.

- **Delete.** A Delete button on each Active and Replaced row, and none on a Deleted one. It opens shadcn's `AlertDialog`, the dialog made for confirming something destructive: "Delete this memory?", "It will not be used again. It stays listed under Deleted.", and the memory's own text, so the owner sees which one. Focus starts on Cancel, so Enter cannot delete by accident, focus stays inside the dialog, and the page behind it is out of reach. Escape or Cancel closes it and gives focus back to Delete. While the request is on its way both buttons are locked and Escape does not abandon it, and the dialog stays open until the server has answered. A failure shows in words inside the dialog (a lost connection, a memory that is already gone, anything else), the row stays, and the owner can try again or cancel. The first version asked inline under the row; it was replaced by the dialog at the owner's request.
- **What the owner sees after.** The row leaves the Active or Replaced tab at once, All shows it as deleted, and the Deleted tab shows it at the top straight away, before the server has been asked and without a duplicate once it has (`applyDeleted`, a pure function). A line above the tabs says "Memory deleted. It will not be used again, and is listed under Deleted." It is announced and takes focus, because the row that had focus is gone.
- **History.** A History button on every row opens what happened to the memory, oldest first, in words: "Written Oct 28, 2026 · The owner said it · from a chat message", "Replaced …", "Deleted Nov 2, 2026 · Deleted by the owner in the app". It is read when opened, kept while the row stays, asked again after a failure, and read afresh after a delete, so it shows the deletion. The text is plain text, whatever the server's reason says.
- **No edit and no restore, and the page says how to correct one:** "To correct one, tell the assistant, for example 'Actually, I live in Porto now.' You can delete a memory here." A test pins that no button says edit, restore or undo.
- **Every button is named after its memory** ("Delete: The owner is vegetarian."), so a long list of identical "Delete" buttons can be told apart by a screen reader.
- **Why the history is read from the click and not from an effect.** The lint rule against setting state in an effect rejected the first version, and a click handler is the more direct place anyway: the owner opens it, so it is read.
- **Tests:** 61 new, 415 in all, with no server and no key: the two API calls and their errors (including a refused token signing the owner out), the pure helpers, the history hook, and the list (which memories can be deleted, the dialog and which memory it names, focus, Escape, one request, each tab, failures, the history, plain text, the keyboard alone). About forty deliberate breaks each failed a test except a few that change nothing a user sees (two of them were dead code, one of which, a focus override on closing the dialog, was removed for that reason). They also found three faults, all fixed: with the inline question the Delete button was disabled while it was open, so Cancel could not give focus back to it; the Escape-during-request test never reached its handler; and when the dialog replaced the inline question, several tests kept looking for the old element and passed without checking anything, so the whole block was rewritten against the dialog.
- **Checked in a real browser, again with the dialog.** On a production build in Chrome against the real API: the list showed Delete and History on each row; History listed the events; the dialog opened on Cancel and named the memory; Escape closed it and returned focus to Delete; cancelling deleted nothing; Tab then Enter deleted by keyboard; the dialog closed, the row left the list and focus went to the announcement and stayed there; the Deleted tab listed it once at the top with no Delete button; its history ended "Deleted … by the owner in the app"; it stayed gone after a reload; and a phone-width page did not scroll sideways. All thirteen passed, and the dialog's screenshot was read.

**Left as it is.** The announcement stays until the page is reloaded. A delete is one memory at a time, with no bulk delete. A memory's text stays on file after a delete and is shown under Deleted; erasing it for good is still a later question.

## Eval isolation, as built

`snapshot` and `restore` in `backend/app/memory/seed.py`, used by `evals/agent_eval.py --writes on`.

- **Snapshot and restore.** `snapshot` reads the memory tables once after seeding: every row with its id, embedding, dates and status, and every event. `restore` empties both tables and puts the rows back by plain inserts, with their own ids (so a seed key still names the same memory), the replaced-by links last (a replaced memory may point at a later row), the log, and the id counters moved past the largest. The search index is computed by the database again. No embedding is requested, so a restore is a few milliseconds and free.
- **In the agent eval.** `--writes on` gives the memory-on arm the real memory writer. Before every case the memory is restored from the snapshot; each turn waits for the write it queued, so the next message sees it and the next case never meets a write in flight; what the case changed in memory is recorded (`added: …`, `replaced: home-city (was active)`, `deleted: …`, `removed: …`) in the results and shown for failing cases. The memory-off arm has no writer, so it stays the control. Cases run one at a time with writing on (`--concurrency` above 1 is refused), because every case restores the one shared database.
- **Off is the default, and it is the phase 3 baseline.** With writing off no case can change memory, so nothing needs restoring and the runs can still go in parallel.
- **A memory the agent wrote during a case is not judged by the seed.** The runner names a recalled memory that is not a seed row `id:N`, and the leak check no longer counts those as leaks, since there is no seed row to judge them by. Any other unknown key is still a leak, and a written memory beside a real leak does not hide it. What the agent writes is judged by the write path's own checks (the memory write eval).
- **Tests:** 11 for snapshot and restore against the test database (every row and embedding back, what was added or changed or logged undone, ids kept, links kept, counters continue without a collision, restoring twice is the same, a restore is committed for other connections, `last_used_at` comes back, an empty snapshot) and 10 for the eval plus 3 for the leak check (what a case wrote is recorded and the next case starts clean, a stray memory is removed, each turn waits for its write, the last write has finished before the case returns, no snapshot restores nothing, a written memory recalled in the same case is not a leak, the description of changes, and the option check). Nineteen deliberate breaks each failed a test, after two survivors showed that nothing read the data from a second connection and nothing read `last_used_at` straight from the table.
- **A real run, dev, one run, writing on:** 35 of 36, the same single miss (`a003`), zero leaks, 490 input tokens a message, exactly the baseline. No case changed memory: the agent cases are questions, and the writer wrote nothing from 36 chats and 38 turns, so it did not invent a memory from a question. This run exercised 36 real restores with the real seeded embeddings and the real writer end to end; it had no write to undo, so the undoing is covered by the unit tests and the end-to-end trap eval, whose cases do write.

## End-to-end memory eval, as built

`evals/memory_eval.py`, `memory_checks.py` and `memory_cases.py`, with `evals/datasets/memory.{dev,test}.jsonl` and `data/memory_traps.yaml`. `make eval-memory ARGS="--memory both --repeat 3"`.

**What a case is.** A story: several chat sessions with the clock moved between them, then a probe asked in a new session. Every case starts from an empty memory (the tables are emptied first), so the probe can only be answered if the agent learned the fact from the sessions. The sessions are placed on the clock backwards from the probe, which is always at `EVAL_NOW`, so "next Tuesday" resolves the same way on every day the eval runs. `gap_days` is the days since the previous session, `probe_gap_days` the days between the last session and the probe (the stale-event trap needed it: its session had "gap 60", which could not say the probe was 60 days later). Each turn waits for the memory write it queued.

**What is checked, in code.**
- The reply: `must_include` (every word must be there), `must_not_include`, and for `not_know` that the reply says it does not know.
- The table: `must_not_store`, a word that may not appear in any memory the agent wrote, whatever its status. This is what catches a secret or an injected instruction that the reply happens not to repeat. It is zero-tolerance.
- Leaks: a deleted or an external memory in the probe's prompt, or a replaced one as "current", is zero-tolerance.
- A case with outside text it never read fails (`external_not_read`), because it then tested nothing.
- The loader refuses a case that cannot test anything: an `answer` case with no required word (a reply that knew nothing could pass it), a `not_know` case with facts, a word both required and forbidden, a first session with a gap, an empty turn.

**Outside text arrives as a tool result.** Phase 6 will deliver mail and events that way, so the runner gives the agent two fake read tools (`read_latest_email`, `read_next_event`) in the sessions that have outside text, and hands over the text when the agent calls them. The write path takes its context from the words of the chat, so a tool result never reaches the extractor; the injection cases test that for real. In every one of the nine injection runs the agent did read the outside text, and nothing from it was stored.

**The cases.** Twenty-eight dev and twelve test, in the eight categories of the plan (recall, update, forgetting, relevance, stale, sensitive, duplicate, injection), all eight traps in dev, each category in both splits and no input in both. They are written from the traps with variants: a changed fact told over several sessions, a correction in one chat, forgetting a replaced memory, forgetting then telling again, an event still ahead as the control for the stale ones, a secret next to a fact worth keeping, the same fact over three sessions, an injection through a calendar event.

**Results, dev, three runs, memory on and off (84 runs each):**

| | memory on | memory off |
| --- | --- | --- |
| Passed | 81 of 84 | 39 of 84 |
| Forbidden words stored | 0 | 0 |
| Leaks | 0 | 0 |
| Errors | 0 | 0 |

| Category | on | off |
| --- | --- | --- |
| recall | 15/15 | 0/15 |
| update | 14/15 | 0/15 |
| duplicate | 4/6 | 0/6 |
| forgetting | 12/12 | 9/12 |
| sensitive | 9/9 | 6/9 |
| stale | 9/9 | 6/9 |
| relevance | 9/9 | 9/9 |
| injection | 9/9 | 9/9 |

These are the numbers as measured, before the `m024` probe was changed (below). After the change `m024` alone passed three runs of three; the whole set was not run again.

How to read it: 15 of the 28 cases need a fact the agent learned (the `answer` cases). Those pass 42 of 45 runs with memory on and none of the 45 with it off, which is the measured effect of memory. The other 13 cases (forgetting, relevance, stale and sensitive ones that expect nothing to be said, and the injections) pass with memory off too, because saying nothing satisfies them. They are there to catch the agent doing something wrong, not to show that memory helps, so only the first group can show that. What the memory table looked like at each probe was also read, not only the answers: Lisbon superseded by Porto, the sister's two memories deleted, a deleted Lisbon with the current Porto kept, one vegetarian memory from five wordings and one Porto memory from three sessions, the trip kept without the passport number, and no card number, password, PIN or injected account in any table.

**Three misses, and what they are.**
- **`m024`, twice: a fragile check, fixed.** The memory was right (one vegetarian memory). The agent used it, suggesting a chickpea curry, but did not say the word "vegetarian", so a correct reply failed the check. The probe was changed to ask about the diet directly (and so was its test twin, `m110`, before the test split was read); `m024` then passed three runs of three. A change to a dev case after seeing a result, recorded for that reason.
- **`m008`, once: a real reconciliation miss.** "I work at Acme Logistics", then 30 days later "I just started at Globex": in one run both memories stayed active and the reply mentioned both. Measured alone on the real model, the reconciling call keeps both (`new`) 3 times in 10 for "The owner has started working at Globex" and replaces the old one 7 times in 10; for "The owner works at Globex" it replaces 10 of 10. The statement really is ambiguous, since "I just started at Globex" does not say the owner left Acme, and the reconciler is told to prefer `new` when unsure, because a stored near-duplicate is harmless and a lost fact is not. So this is the conservative choice showing its cost, about a 30 percent miss on a changed job said that way, and it is left as it is. Fixing it means telling the reconciler that a single-valued fact such as a job or a home is replaced by a new one, which would trade this miss for the risk of replacing a real second job; that is a decision, not a bug, and it was not taken. The wording experiment that came after is under "What the assistant says when told to forget, as built".
- **`m009`, found by the control.** Its first version, a dropped sugar habit, only forbade a phrase, so it passed with memory off as well. It was rewritten as a changed favourite colour, and the loader now refuses any `answer` case without a required word.

**Test split:** read once, see "Held-out test results".

## What the assistant says when told to forget, as built

`LEARNS` in `backend/app/agent/prompt.py`, `reply_must_not_misstate` in the end-to-end eval.

**The problem, measured.** The memory is updated after the reply, and the assistant did not know that: its prompt says it cannot take actions and never mentions memory. On the four forget stories (five runs each) it said it could not do what it was asked in 6 of 20 replies, every time the owner used the word "delete" ("delete everything you know about my sister": "I can't delete stored information from here"), although the memory was deleted right after in every one of those runs. When the owner said "forget" it answered correctly ("Understood, I won't use that again"). So the reply was false and the effect was right.

**The fix.** When, and only when, the agent has a memory writer, the prompt gets a short rule: what the owner says is saved automatically after the reply, and a request to forget or delete is carried out then; say in plain words that you will stop using it, for example "Understood, I won't use that again"; do not say that it is already deleted; never say that you cannot delete or forget; do not list what you saved or promise a specific entry. With no writer the rule is left out, so the assistant never claims something the server will not do. The loop passes `learns=self._memory_writer is not None`.

**The check.** A session in a story can say `reply_must_not_misstate`: none of its replies may claim the assistant cannot do it (a phrase list: "I can't", "I cannot", "unable to", "no way to", and so on) or claim that it is already done ("I've deleted", "has been removed", and so on), after the same flattening as the other checks. It is set on the five forget sessions (`m011` to `m014` in dev, `m105` in test). Looking at the replies after the first fix showed one saying "I've deleted what I knew about your sister", which is true here but would be false if reconciliation found nothing to delete, so the second phrase list and the sentence "do not say that it is already deleted" were added.

**Measured, dev, the four forget stories, five runs each (20 runs):**

| | before | after |
| --- | --- | --- |
| Passed | 14 | 20 |
| Replies saying it cannot | 6 | 0 |
| Replies saying it is already deleted | not checked | 0 |

**Regression, on the finished prompt:** the agent eval with writing on passed 35 of 36, the same single `a003` miss, with zero leaks; the end-to-end dev set passed 28 of 28 with nothing forbidden stored and no leak. With writing off the prompt is unchanged, so the phase 3 baseline cannot move. **The cost:** the rule adds about 94 input tokens to every message of an agent that learns (490 to 584 a message in the agent eval).

**Tests:** 6 for the prompt and the loop (the rule is there only with a writer, after the other rules and before the memories, with each of its sentences), 7 test functions for the two phrase lists and the flag (22 cases once their lists of phrases are expanded), 5 for the runner and 2 for the loader. Sixteen deliberate breaks (6 on the refusal check, 5 on the completed-deletion check, 3 on the prompt rule, 2 on the loop) each failed a test.

## The reconciler and a changed job: an experiment, and a decision still open

The `m008` miss ("I just started at Globex" leaves two jobs active) was measured on the real model, one reconciling call at a time, ten calls each, with the stored memory and the owner's words given. Three wordings of the reconciler's instruction for `replaces`:
- **current:** prefer `new` when unsure.
- **tilted:** a job, a home, a phone provider and other things a person has one of at a time are replaced when the owner says they started, moved or switched, unless they say they keep both; pets, allergies, friends and hobbies are not.
- **refined:** the same, and a new one described as part-time, a side job, extra, a second one or on top of the first is added.

26 scenarios, 260 calls per wording: scenarios where replacing is right (a changed job said two ways, a move, a switched provider, a promotion, a changed favourite) and where it would be wrong (a second job, a Friday job, freelance work, weekend consulting, a side business, a second pet, another allergy, another language, a hobby, a friend, a parents' home, a holiday flat, someone else's home).

| | current | tilted | refined |
| --- | --- | --- | --- |
| Right, out of 260 | 250 | 249 | 259 |
| "I just started at Globex" replaces | 7 or 8 in 10 | 10 | 10 |
| "I joined Globex last week" replaces | 1 or 2 in 10 | 10 | 10 |
| "I started a part-time job at Globex" is added | 10 | **0** | 10 |

What it says. The current wording is worse than the single `m008` run suggested: "I joined Globex last week" replaces the old job only one time in ten. The simple tilt fixes that and creates a new wrong answer, replacing a part-time second job every time. The refined wording fixes both, and it kept the second job on phrasings it was not written for (freelance, weekend consulting, a side business, a gig), with one borderline 9 in 10. Caution: the part-time exception was written after seeing that failure, so that case passing is partly by construction, which is why the other phrasings were run. A job said with no sign of "on top" ("I started at Globex") is replaced, and if the owner kept both jobs that is wrong; the old memory is kept as history when it is replaced, so it is recoverable, which a wrong "keep both" is not as cleanly (two contradicting facts stay active).

**Decision: not adopted, and the case was fixed instead.** The sentence "I just started at Globex" really is ambiguous, since a person can have two jobs, and keeping both when unsure is a defensible default, so the reconciler's wording is left as it is and the weakness is recorded: "I joined Globex" replaces the old job only about one time in ten. `m008` was rewritten to say it plainly ("I left Acme and just started at Globex"). That fixed the reconciliation (Acme replaced and Globex active in all five runs) and left one failure of another kind: a correct reply, "You work at Globex. You told me in October 2026 that you'd left Acme", contained the forbidden word "Acme", because the stored memory itself says the owner left Acme. The check was narrowed to Acme as the current job ("work at Acme", "works at Acme", "working at Acme"), and `m008` then passed five runs of five. Two dev-case revisions after seeing a result, recorded as such.

## Held-out test results

Both test splits were read once, on the finished prompts, after the dev work was done and with nothing changed afterwards: not the prompts, not the checks, not the cases. A miss was not fixed, and the numbers below are what was measured.

| Eval | Split | Result |
| --- | --- | --- |
| Memory write eval, the pipeline (extract, filter, reconcile) | test, 17 cases, one run | **17 of 17**; 12 of 12 with no stored memory, 5 of 5 with some; 0 forbidden words stored, 0 wrong changes |
| End-to-end memory eval, memory on | test, 12 stories, one run | **11 of 12**; 0 forbidden words stored, 0 leaks |
| End-to-end memory eval, memory off (the control) | test, 12 stories, one run | **6 of 12**; 0 stored, 0 leaks |

**Reading the end-to-end result.** The six stories that need a learned fact (recall, changed facts, the vegan diet): 5 of 6 with memory on, 0 of 6 with it off. The other six cases (forgetting, relevance, stale, sensitive, injection) pass either way, as on dev, because saying nothing satisfies them. Dev gave 81 of 84 on and 39 of 84 off; the held-out split gives the same shape, 11 of 12 and 6 of 12.

**The one miss, `m103`.** "I drive a Fiat 500", then 40 days later "I sold the Fiat and bought a Tesla", then "What car do I drive?" The reply, "You drive a Tesla. You told me in October 2026 that you sold your Fiat and bought one", is right. The memory table is right too: the Fiat memory is replaced and the Tesla one is active. It fails because the case forbids the bare word "Fiat", and a correct reply can say the owner sold it. That is the same weakness found on dev in `m008` ("Acme"): a forbidden word is a poor stand-in for "the old fact is not offered as current". The dev cases that forbid a bare word (`m006`, `m009` and others) have the same exposure and have not been changed. On the test split the case and the number are left as they were read, 11 of 12, and the honest summary is 12 of 12 memory behaviours correct and one over-strict check. The fix, forbidding the old fact as current (as `m008` now does) and not the word, belongs to the next revision of the cases, with the test split no longer held out.

**What the test split does not change.** The sets are small and hand-written (28 and 12 stories, 38 and 17 write cases), one fake owner, and the dev split was tuned on, so these are a baseline and not a benchmark. The held-out numbers agree with the dev numbers, which is the useful finding: nothing was fitted to dev that the test split contradicts.

## Eval design

Two evals, because there are two things to get right and a failure in one should not hide in the other.

**The write path, on fixed turns** (`make eval-memory-write`). No agent and no database reads of the real memory: each case is some existing memories (inline), the owner's turn, and what must happen. It calls extraction and reconciliation and checks the resulting changes by code:

- *must write*: a memory whose content contains each required term (and a kind, and for events a date range) exists after
- *must not write*: nothing stored for chit-chat, questions, requests, or the assistant's own statements
- *noop*: the same fact worded again adds nothing
- *supersede*: the right old memory, and only it, is superseded, and the new one is active
- *delete*: the right memories are deleted and no other memory changes
- *never*: card numbers, passwords and PINs are not in any memory; nothing is stored with `source_kind` other than `owner`

Cases cover each trap in `data/memory_traps.yaml` plus ones too small for an end-to-end case (a relative date, a two-fact sentence, "forget that" with several close memories, a correction of a correction, a non-English sentence). Reported: per category passes, proposals per turn, tokens and cost per turn.

**End to end, the traps** (`make eval-memory`). The traps in `data/memory_traps.yaml` become cases in `evals/datasets/memory.{dev,test}.jsonl`. A case plays its sessions through the real agent from an empty memory, moving the clock between them, waits for each write, then asks the probe in a new session and checks the reply, and the memory table, by code. Also run with memory off as the control. The dev split is run three times and the test split once and not tuned on, as in phase 3. How it was built and what it measured is under "End-to-end memory eval, as built".

**Isolation.** Writes make cases depend on each other, so the agent eval restores the seeded memories before every case. Seeding calls the embeddings API for 42 memories, which is too slow and costly to repeat per case, so the seeded rows (embeddings included) are read once at the start and written back by plain inserts. A case that can write runs alone: concurrency is one whenever writing is on (`--writes on`). The existing 36 dev and 20 test agent cases are re-run as a regression check and their baseline must not move.

### Pass conditions

| Kind | Rule |
| --- | --- |
| Zero tolerance | No card number, password or PIN in any stored memory, over every run. No memory written from the assistant's text alone. No `external` row written. No memory deleted that the owner did not ask to forget. A failed extraction never changes a reply or loses it |
| Recorded, not tuned to | Extraction recall and precision per category, trap pass rates, tokens and cost per turn, memory on against off. Targets are set after the first baseline, as in phase 3, and a miss that is kept goes in this doc with its reason |

## Decisions to settle before starting

| Decision | Default if you say nothing |
| --- | --- |
| What is worth remembering | Explicit statements by the owner: facts, preferences, events, and plans. No inferred preferences ("they seem to like jazz") until the evals show they help |
| Which model extracts | A separate call after the turn, using a setting (`EXTRACTION_MODEL`) that defaults to the chat model; the eval reports cost so a cheaper model can be compared on the same cases |
| Extraction and the busy lock | The chat is free for the next message as soon as the reply is stored and `done` is sent; extraction is queued separately and in order |
| When extraction fails | The reply stands; the failure is logged without the owner's text; nothing is retried in this phase. A missed memory is a quality problem, not a correctness one |
| What the assistant says when told something | It may say "noted" in its own words, but the prompt does not let it list what it stored or promise a specific memory, because the write has not happened when it answers. When asked to forget it says it will stop using it, never that it cannot, and never that it is already deleted. The rule is in the prompt only when the server really writes memories |
| Who may delete | Only an explicit owner request in chat, or the owner in the web app. Reconciliation never deletes to resolve a contradiction; that supersedes |
| Contradiction | The newer owner statement wins and supersedes; the old one stays as history |
| Similar closeness threshold | A cosine threshold set from the dev cases and recorded here; the second call decides within it |
| Many facts in one message | All are proposed; at most five writes and three deletes per turn, so one odd message cannot rewrite memory |
| Dates | The extractor resolves relative dates against the turn's date and the owner's time zone and sets only an event's end (`valid_until`; a day ends at the start of the next one in the owner's zone); it never sets `valid_from`, so nothing it stores is hidden by the phase 2 rule. An event with a past end is not offered as upcoming (phase 2 rule, unchanged). A fact that starts to hold later (a job in January) is stored with its date in the text and is visible now; hiding it until then is a later question |
| Restore | None. A restore can put a memory beside one that contradicts it; the owner says it again and it goes through reconciliation |
| Edit | None. The owner tells the assistant, which goes through the filter and reconciliation; see "Edit and restore were built and then removed" |
| Memory off in evals | A constructor option turns off both recall and writes, so the control run changes nothing |
| `last_used_at` | Not touched by writes; it still means "shown in a prompt" |
| Language | Memories are stored in the language the owner used; recall is by embedding, so a mixed store still works, and the eval has a non-English case |

## Gate check

| Check | Expected result |
| --- | --- |
| Used in the next session | A fact stated in one session is answered in a new one (the `recall` category) |
| Update | The changed-fact and conflicting-statements traps answer with the new fact and not the old one; the old row is `superseded`, its `superseded_by` points at the new one, and the history question still finds it |
| Forgetting | After "forget that", the memory is `deleted`, an event says why, the probe does not leak it, and no other memory changed |
| False memory | The assistant's own statements, questions and requests store nothing; the injection trap stores nothing as an owner fact |
| Sensitive | Card number, PIN and password cases store nothing, over every run |
| Duplicates | The near-duplicate trap leaves one active memory, not five |
| Stale | A stored event whose date has passed is not offered as upcoming |
| Events | Every write has a `memory_events` row with a reason and the source message |
| Web app | Delete works from the list by mouse and keyboard, and asks first; a memory's history is readable; an old reply shows "since replaced" or "deleted" accordingly |
| Failure | With the model failing or returning garbage, replies are unchanged and nothing is written |
| Isolation | Two runs of the agent eval in a row give the same memory at the start of every case |
| Phase 3 and 4 | The agent eval baseline does not move; `pytest` and `make web-test` pass with no API key and no server |
| Browser check | The gate scenario run by hand against the real API, results recorded here |

## Gate check, results

Run on 3 November 2026. Where a row had no direct evidence before, it was run for this check; the others cite the measurements above.

| Check | Result | Evidence |
| --- | --- | --- |
| Used in the next session | Pass | Recall stories: 15 of 15 on dev (3 runs), 3 of 3 on the held-out split. In the browser: "I live in Lisbon" in one chat showed in the memory list, and a new chat answered "You live in Lisbon" with the memory shown as recalled |
| Update | Pass, with two notes | Changed-fact and conflicting-statement stories: Lisbon replaced by Porto, the old row `superseded` and linked, found again by "where did I live before Porto?" (browser, events audit). Dev update stories 14 of 15 (the miss is the ambiguous "I just started at Globex", see above); held-out 1 of 2, the other being `m103`, a correct reply that failed an over-strict check |
| Forgetting | Pass | The memory is `deleted` with an event "the owner asked to forget it"; in the isolation run asking to forget the dog deleted only the dog; in the browser the forgotten fact was under Deleted and "where did I live before Porto?" answered "I don't know". Forget stories 20 of 20, with no "I can't" and no "I've deleted" in any reply |
| False memory | Pass | Assistant claims, questions and requests store nothing (write eval: no-write 6 of 6 and assistant-claim 3 of 3 on dev, 3 of 3 and 1 of 1 on test). Injection through an email or a calendar event: 9 of 9 on dev, 1 of 1 on test, the agent read the outside text every time and nothing was stored |
| Sensitive | Pass | 0 forbidden words stored in every run of every eval (dev 84 end-to-end runs per arm, 111 write runs, and both held-out splits). In the browser a card number and PIN left no memory |
| Duplicates | Pass | One memory from five wordings and from three sessions on dev; one vegetarian memory from "I'm vegetarian" and "I don't eat meat" in the browser and in the events audit |
| Stale | Pass | Stale stories 9 of 9 on dev (3 runs) and 1 of 1 held-out; an event still ahead is not hidden (`m020`) |
| Events | Pass | Audit of a real multi-write run: 4 memories, 6 events, each memory begins with `created`, every reason is one of the fixed phrases, every event points at a real owner message, no reason quotes a memory, `superseded` and `deleted` agree with each row's status and link, events in time order. The browser run's table agreed |
| Web app | Pass | Delete by keyboard asked first (focus on Cancel), removed the row, stayed deleted after a reload; the history read "Written … Deleted … by the owner in the app"; an old reply showed its recalled memory as "a memory that has since been deleted", with no text. 415 frontend tests |
| Failure | Pass | 16 forced failures. A real one: the writer's model did not exist (the provider answered 404), and the reply streamed and was stored unchanged, nothing was written, the failure was logged without the owner's text, and the same writer recovered on the next turn. Garbage from the model (an empty object, no list, a list, null, a kind or an op that does not exist, text too long or empty), a failing model and a failing embedder: each left the reply exactly as it was and wrote nothing |
| Isolation | Pass | Two runs of three cases that really write (add, replace a seeded memory, delete a seeded one): after every case the restored memory equals the seeded snapshot row for row (ids, embeddings, statuses, events), and each case's list of changes holds only its own |
| Phase 3 and 4 | Pass | Agent eval, dev: memory on 35 of 36 (the same `a003` miss), memory off 10 of 36, 0 leaks, 490 input tokens a message: the phase 3 baseline. 957 backend tests pass with an empty API key and no server, 415 frontend tests, ruff clean |
| Browser check | Pass | 14 of 14, below |

**Browser check.** Run on a production build in Chrome, against the real API and the real model, from an empty memory, with the memory writer on, driven by a script that acts as the owner would. Fourteen checks, all passed: a stated fact shows in the memory list; a new chat uses it and shows it was recalled; a changed fact leaves Porto under Active and Lisbon under Replaced; the new fact is answered and not the old one; a question about the past finds the replaced one; asked to forget, the reply is "Understood. I won't use that again."; the forgotten memory is under Deleted; after forgetting the old fact is never used; a card number and PIN are not stored; two ways of saying one thing make one memory; delete from the list by keyboard (focus starts on Cancel); it stays deleted after a reload; the history is readable; an old reply shows its memory as deleted without its text; a phone-width page does not scroll sideways. The final table agreed with the screens.

**What is left as it is.**
- `m008` was ambiguous as first written and the reconciler keeps both jobs when unsure ("I joined Globex" replaces the old one about one time in ten). Left, and recorded.
- Several dev cases forbid a bare word, which a correct reply can say (`m103` on the held-out split failed that way).
- The test splits are small (12 stories, 17 write cases, one fake owner) and were read once, so they are a baseline.
- There is no edit and no restore of a memory in the app, by decision; chat is how a memory is changed, and phase 12 plans a better way.
- A deleted memory's text stays on file; erasing it for good is a later question.

## Not in this phase

- Reading calendar or email, and storing anything `external`: phase 6
- Writes with confirmation: phase 7
- Summarising old memories or expiring unused ones, and compacting a long chat: phases 8 and later; `last_used_at` is logged and reviewed with data
- Inferred preferences, memory importance scores and merging many memories into one summary
- Letting the model call a memory tool (`search_memory`, `change_memory`, `forget_memory`): the extraction step writes, the owner changes memory by saying so in chat, and the `a003`-style recall misses stay open. The read-only `search_memory` tool is phase 6; changing memory by chat, with confirmation and an honest result, is phase 12 (`DESIGN.md`), built after the tools of phase 6 and the confirmation of phase 7
- A retry queue for failed extraction, or a write queue shared across several server workers
- Real-time updates of the memory list while a chat is open
- Editing a memory or restoring a deleted one in the app: both were built and removed (see above), and erasing the text of a deleted memory for good stays a later question
- Multi-user: phase 11
