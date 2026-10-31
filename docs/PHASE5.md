# Phase 5: Memory writes

**Status:** planned
**Gate:** a fact the owner states in one session is used in the next; a changed fact replaces the old one, a forgotten one is never used again, and nothing that should not be stored is stored

## Goal

Until now memory was read-only: the seed file filled it and the agent only recalled. This phase lets the agent learn. After each turn a separate step reads what the owner said and decides what is worth keeping, compares it with what is already stored, and writes the change. The owner can also correct memory by hand in the web app: edit a memory, delete it, bring a deleted one back.

Writing is where memory goes wrong in ways reading cannot: it stores things it should not (a card number, a passing remark, text from an email), it stores the same fact five times, it keeps the old city next to the new one, and it deletes the wrong memory when told to forget. So the work is ordered evals first. The extraction eval and the trap cases are written before the extractor, and a few of their checks are zero-tolerance gates, not numbers to improve.

Two pieces of earlier phases are completed here. Phase 3 seeds the memory once per eval run, which is only safe while no case can write; from now on every agent-eval case starts from the same memory. And phase 4's memory list gets its first controls.

## Deliverables

- [ ] **Extraction eval and cases first**: `evals/extraction_cases.py`, `evals/extraction_eval.py`, `evals/datasets/extraction.{dev,test}.jsonl`. Fixed turns, each with the memory changes it must and must not produce, checked by code. Written, and run against a stub that stores nothing (the baseline), before the extractor exists
- [ ] **Extractor**: `backend/app/memory/extract.py`. One model call after a turn: the owner's messages in, proposed changes out (`add`, `delete`) as structured output, validated before anything uses them
- [ ] **Reconciliation**: `backend/app/memory/reconcile.py`. Each proposal is compared with the closest active memories; the result is `noop`, `add`, `supersede(id)` or `delete(id)`, and the rules that keep it safe are code, not prompt
- [ ] **Write functions with events**: `backend/app/memory/write.py`. `add`, `supersede`, `delete`, `restore`, each in one transaction with its `memory_events` row and the reason, so any change can be explained and undone
- [ ] **Filter before write**: `backend/app/memory/filter.py`. Card numbers, passwords, PINs and long identifiers are dropped in code whatever the model proposed
- [ ] **Agent integration**: the loop starts extraction after the reply is stored and `done` is sent; a failure there never touches the reply; a constructor option turns it off for the memory-off eval
- [ ] **Memory API**: `PATCH /memories/{id}` (edit), `DELETE /memories/{id}`, `POST /memories/{id}/restore`, `GET /memories/{id}/events`
- [ ] **Memory list controls**: edit, delete and restore in the web app, with the history of one memory (when it was written, from which message, what replaced it)
- [ ] **Eval isolation**: the agent eval restores the seeded memories before every case, without calling the embeddings API again
- [ ] **Memory eval**: `evals/memory_eval.py` plays the trap cases (sessions, gaps, probe) against the real agent, memory on and off, dev three times and test once
- [ ] **Tests**: unit tests with a fake model for extraction, reconciliation, the filter, the write functions, the API and the loop. None needs an API key
- [ ] **Docs and baselines**: results recorded here, `DESIGN.md` and the README updated, and the open decisions below settled

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

owner edits in the web app --> PATCH / DELETE / restore --> the same write functions
```

Extraction runs after the reply, so it adds no latency to the answer. The cost is that it can still be running when the owner sends the next message. Extractions go through one queue and run in order, so a second one reconciles against what the first wrote, and a recall that races a write sees the memory one message late, which is acceptable. The queue lives in the one worker process, like the turn lock (see `PHASE3.md`).

### Extraction

The input is the owner's messages of the turn, with the last few earlier messages as context. The assistant's reply is shown as context, labelled as the assistant's, and is never a source: a fact the model said and the owner did not is not stored. Tool results are never in the input (there are none yet; phase 6 keeps them out, and anything from an outside source can only be stored as `external`).

The call returns a list of proposals, each one `{op, kind, content, valid_from, valid_until}`:

- `add`: one self-contained sentence in the third person ("The owner has a dentist appointment on 3 March 2027"), with a kind (`fact`, `preference`, `event`, `episode`) and dates when the owner gave or implied them. The call is given today's date and the owner's time zone, so "next Friday" becomes a date
- `delete`: a sentence describing what the owner asked to forget, not an id; finding the memory is reconciliation's job

Questions, greetings, small talk, requests ("book me a table"), and what the assistant said produce nothing. The empty list is the usual answer.

### Reconciliation

For each proposal the nearest active memories are found (vector search over active, owner memories, the same filter recall uses, so a replaced or deleted memory is never a candidate). Then:

| Case | What happens |
| --- | --- |
| No candidate closer than the threshold | `add`, with no second model call |
| Candidates exist | One call with the proposal and up to five candidates, each with its id and date; it answers `same` (noop), `replaces` plus an id (supersede), `new` (add), or for a delete the ids that match |
| The model names an id that was not shown | The proposal is dropped; ids are checked in code |
| A delete matches nothing | Nothing is deleted, and nothing is stored in its place |
| A delete matches several | Only the ones the model names and that were shown; the number is capped per turn |

Supersede writes a new row, sets the old one `superseded` with `superseded_by`, and sets the old row's `valid_until` to the new one's `valid_from` (or the time of the message), so history questions still work. Nothing is overwritten and nothing is hard-deleted.

Because the call that reconciles sees the candidates' ids, this is the one place memory ids go to a model. The reply's prompt still never contains them.

### Filter

Dropped in code, after extraction and before reconciliation: card numbers (a digit run that passes the Luhn check), IBAN-like strings, anything near the words password, passcode, PIN or secret, and long digit or character runs that look like an identifier or key. A dropped proposal is logged as a count, never with its text, so the log does not become the store. The extraction prompt also says not to propose these; the filter is the part that is tested as a guarantee.

### What a write records

`memory_events` already allows `created`, `superseded`, `deleted` and `restored`. Each write adds a row with the memory id, the event, a reason in words ("owner said they moved", "owner asked to forget", "edited in the web app"). A proposal that ends as a no-op writes nothing and so has no event, and the message id it came from. `source_message_id` on the memory is the owner's message and `source_kind` is `owner`. This phase never writes `external`.

An edit in the web app is a supersede (a new row with the new text, the old one kept as history, reason "edited by the owner"), so an edit can be undone by reading the history, and recalled memories shown under old replies resolve to "since replaced". A restore sets a deleted memory back to `active`. A superseded memory cannot be restored; the owner edits the current one instead.

## Memory API

All behind the owner's login, like the rest.

| Call | Does |
| --- | --- |
| `PATCH /memories/{id}` `{content}` | Supersede an active memory with the new text; returns the new row. 404 for an unknown id, 409 for a memory that is not active, 422 for empty or over-long text |
| `DELETE /memories/{id}` | Status `deleted`; idempotent (deleting a deleted one is a 204) |
| `POST /memories/{id}/restore` | A deleted memory becomes active again; 409 for any other status |
| `GET /memories/{id}/events` | The log for one memory, oldest first, with reasons and message ids |

An edit is embedded like any new memory; if the embeddings API fails the edit is refused with a clear code, not stored without an embedding.

## Web app

The memory list from phase 4 gains, on the Active tab, an edit and a delete control on each row, on the Deleted tab a restore control, and a history view per memory (a collapsible under the row, loaded on open). Delete asks first and says what it does ("This memory will not be used again. You can restore it from Deleted."). Editing is inline, with the same length limit as a message. Controls are keyboard-reachable, and after an action the list updates in place and the row's new state is announced. Replaced and deleted rows keep their read-only look apart from restore.

The chat view needs no change: a reply recalled memories by id, and a memory edited or deleted since already shows as "since replaced" or "deleted" (phase 4).

## Eval design

Two evals, because there are two things to get right and a failure in one should not hide in the other.

**Extraction alone, on fixed turns** (`make eval-extraction`). No agent and no database reads of the real memory: each case is some existing memories (inline), the owner's turn, and what must happen. It calls extraction and reconciliation and checks the resulting changes by code:

- *must write*: a memory whose content contains each required term (and a kind, and for events a date range) exists after
- *must not write*: nothing stored for chit-chat, questions, requests, or the assistant's own statements
- *noop*: the same fact worded again adds nothing
- *supersede*: the right old memory, and only it, is superseded, and the new one is active
- *delete*: the right memories are deleted and no other memory changes
- *never*: card numbers, passwords and PINs are not in any memory; nothing is stored with `source_kind` other than `owner`

Cases cover each trap in `data/memory_traps.yaml` plus ones too small for an end-to-end case (a relative date, a two-fact sentence, "forget that" with several close memories, a correction of a correction, a non-English sentence). Reported: per category passes, proposals per turn, tokens and cost per turn.

**End to end, the traps** (`make eval-memory`). The cases in `data/memory_traps.yaml` become `evals/datasets/memory.{dev,test}.jsonl` (the loader already exists). A case plays its sessions through the real agent, moving the clock between sessions by `gap_days` (extraction is given the same clock), waits for the write queue to drain, then asks the probe in a new session and checks the reply by code: `must_include`, `must_not_include`, or "says it does not know". Also run with memory off as the control. The dev split is run three times and the test split once and not tuned on, as in phase 3.

**Isolation.** Writes make cases depend on each other, so the agent eval restores the seeded memories before every case. Seeding calls the embeddings API for 42 memories, which is too slow and costly to repeat per case, so the seeded rows (embeddings included) are read once at the start and written back by plain inserts. A case that can write runs alone: concurrency is one whenever memory is on. The existing 36 dev and 20 test agent cases are re-run as a regression check and their baseline must not move.

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
| What the assistant says when told something | It may say "noted" in its own words, but the prompt does not let it list what it stored or promise a specific memory, because the write has not happened when it answers. A "forget" gets "I'll stop using that" and the owner can check the list |
| Who may delete | Only an explicit owner request in chat, or the owner in the web app. Reconciliation never deletes to resolve a contradiction; that supersedes |
| Contradiction | The newer owner statement wins and supersedes; the old one stays as history |
| Similar closeness threshold | A cosine threshold set from the dev cases and recorded here; the second call decides within it |
| Many facts in one message | All are proposed; at most five writes and three deletes per turn, so one odd message cannot rewrite memory |
| Dates | The extractor resolves relative dates against the turn's date and the owner's time zone; an event with a past end date is not offered as upcoming (phase 2 rule, unchanged) |
| Restore | Only for deleted memories; a replaced memory is edited through its current version |
| Edit | A supersede, with reason "edited by the owner" |
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
| Web app | Edit, delete and restore work from the list by mouse and keyboard; an old reply shows "since replaced" or "deleted" accordingly |
| Failure | With the model failing or returning garbage, replies are unchanged and nothing is written |
| Isolation | Two runs of the agent eval in a row give the same memory at the start of every case |
| Phase 3 and 4 | The agent eval baseline does not move; `pytest` and `make web-test` pass with no API key and no server |
| Browser check | The gate scenario run by hand against the real API, results recorded here |

## Not in this phase

- Reading calendar or email, and storing anything `external`: phase 6
- Writes with confirmation: phase 7
- Summarising old memories or expiring unused ones, and compacting a long chat: phases 8 and later; `last_used_at` is logged and reviewed with data
- Inferred preferences, memory importance scores and merging many memories into one summary
- Letting the model call a memory tool (`search_memory`, `remember`): the extraction step writes, and the `a003`-style recall misses are a phase 6 question
- A retry queue for failed extraction, or a write queue shared across several server workers
- Real-time updates of the memory list while a chat is open
- Multi-user: phase 11
