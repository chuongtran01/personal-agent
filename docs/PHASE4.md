# Phase 4: Web app

**Status:** not started
**Gate:** the owner can log in, chat with the agent and see which memories were recalled for each reply, in a browser

## Goal

Give the API a face. By the end, the owner opens a page, logs in with the password, sees their chats, talks to the agent and watches the reply stream in, sees under each reply which memories were recalled for it (including the ones that were true once and are not any more), and browses the memory list. The web app is a client of the API built in phase 3 and adds no logic of its own: recall, the prompt, the limits and the login all stay on the server.

One piece of the server changes. Today the recalled memories are sent once, in the `memory` event, and then forgotten, so a reloaded chat would show replies with no memories under them. This phase stores the recalled ids with the reply and returns them with the chat, so the view after a reload matches the view while it streamed.

Nothing here writes memories. The memory list is read-only; editing and deleting are part of the write path in phase 5, and the controls for them are added then.

## Deliverables

- [x] **Recalled memories stored with the reply**: `backend/app/agent/messages.py`, `loop.py`, `store.py` and `api/sessions.py`. The reply that ends a turn carries the ids recalled for it, and `GET /sessions/{id}/messages` returns them resolved (kind, content, whether the memory is a past one, its status today). The model never sees these fields
- [x] **Frontend scaffold**: `frontend/`. Next.js (App Router), React and TypeScript, Tailwind CSS and shadcn/ui (set up with its CLI, the components copied into `src/components/ui/`), a rewrite that forwards `/api` to the backend, ESLint and the type check, Vitest with Testing Library, and `make web`, `make web-test` and `make web-build`
- [x] **API client and event reader**: `frontend/src/api/`. A typed `fetch` wrapper that adds the token and turns errors into codes, and a reader that parses the `POST` stream into events. Built first, against a stream recorded from the real API
- [x] **Login**: the password form, the token kept for the tab, and the way back to login when the token is missing or refused
- [x] **Chats**: the chat list, a new chat and the streaming chat view, with the recalled memories under each reply
- [x] **Memory list**: read-only, by status, with the dates, the source and what replaced what
- [ ] **Errors and limits**: every refusal and failure the API can give has a plain message and a way forward
- [ ] **Tests**: `frontend/src/**/*.test.tsx` and the backend tests for the stored ids. None needs an API key or a running server
- [ ] **Hooks and docs**: the pre-commit and pre-push hooks check the frontend when it changed, and the README says how to run both halves
- [ ] **Browser check**: the gate scenario run by hand against the real API, with the results recorded in this doc

## Architecture

```
browser (Next.js server, same origin)
  /api/*  --rewrite-->  FastAPI on :8000   (the /api prefix is dropped)

login      POST /auth/login                       -> token, kept in sessionStorage
chat list  GET  /sessions                         -> sidebar
chat view  GET  /sessions/{id}/messages           -> history, with the recalled memories
           POST /sessions/{id}/messages           -> SSE: memory, text..., done | error
memories   GET  /memories?status=&limit=&offset=  -> the list
```

The browser only ever talks to its own origin and Next forwards `/api` to the backend (a `rewrites()` entry in `next.config.ts`, with the target in `API_URL`, `http://localhost:8000` by default), so there is no CORS setting and the token is never sent across origins. A reply is a stream, so the config also turns compression off and raises the proxy timeout above the server's longest turn. `POST` streaming cannot use `EventSource`, so the reply is read with `fetch` and a `ReadableStream`.

**What Next.js is used for.** It is the app shell: file-based routing, the dev server, the build, fonts and the rewrite. Every page that shows data is a client component (`"use client"`), because the token lives in `sessionStorage`, which a server cannot see, so nothing is rendered or fetched on the server, and there are no Route Handlers and no middleware in this phase. The API stays the only backend and the only thing that checks a token.

Layout:

```
frontend/
  src/api/         http.ts (the shared fetch: prefix, token, error codes), auth.ts, chats.ts and
                   memories.ts (the endpoints, one module per area), sse.ts (the reader), types.ts
  src/auth/        token storage, the guard that sends a visitor without a token to login
  src/app/         the pages: login/, and a signed-in group with the new chat, chats/[id]/ and memories/
  src/components/  ChatList, MessageList, Composer, RecalledMemories, Markdown
  src/components/ui/  the shadcn/ui components in use (Button, Input, Textarea, Tabs, ...), copied in
  src/lib/         chat-state.ts (how a turn's events change a chat, pure), chat-store.ts (every open chat's
                   state, outside React), use-chat.ts, messages.ts (the wording of each error code),
                   limits.ts, time.ts, utils.ts (the `cn` class helper that shadcn components import)
  src/test/        a stream recorded from the real API, helpers that fake fetch
```

shadcn/ui is not a package that is installed and upgraded: its CLI copies each component's source into `src/components/ui/`, where it belongs to this repo and is edited like any other file. They are built on Radix primitives, which bring the keyboard handling, focus management and ARIA that would otherwise be written by hand. `@` is an alias for `src/` in `tsconfig.json`, as `components.json` expects, and a component is added with `npx shadcn@latest add <name>`.

The pieces that decide something (`sse.ts`, `http.ts`, the error mapping) have no React in them, so they are tested without rendering. The pages call them and hold no rules of their own.

## What the server adds

The loop already knows which memories it recalled when it stores the reply. The final assistant message of a turn gets two optional lists, `recalled` and `recalled_past`, of memory ids. A reply stored before this phase has neither and loads as if both were empty; a reply that calls a tool and so is not the end of the turn carries nothing.

`GET /sessions/{id}/messages` then returns, with each assistant message, a `memories` list: `{id, kind, content, past, status}`, resolved when the chat is read. Memories are never overwritten (a change adds a row), so the content shown is the content the model saw. Two cases need care:

- **A memory deleted since.** It is returned with `status: "deleted"` and no `content`: the owner asked for it to be forgotten, and an old reply must not bring its text back. The memory list can still show it, under its own `deleted` tab, because there the owner asks to see them.
- **A memory replaced since.** It is returned with `status: "superseded"` and its text, and the view marks it "since replaced".

The model never sees these fields: the loop stores them, but the input sent to the provider is built from the text and the tool calls only, and a test pins that. The `done` event already carries the id of the stored reply, so a streamed reply and a reloaded one attach the same memories to the same message.

## Screens

**Login.** One password field. A wrong password says so and keeps the field; the `503` that means login is not configured says the server is not set up, without naming settings. After login the visitor goes where they were headed, or to a new chat. Where they were headed travels in the address as `?next=`, which anyone can write, so it is followed only when it is a path on this site: `//host`, `https://host`, `javascript:`, a backslash, a tab the URL parser would drop, and the login page itself all go to the home page. `router.replace` runs `javascript:` URLs, so this is a security check and not a nicety. Someone already signed in who opens the login page is sent on without seeing it.

**Chats.** The layout of a familiar chat app. A sidebar (shadcn's Sidebar). Its toggle is inside it, at the right of its header, and the page has no bar of its own. It folds, with that button or Ctrl/Cmd+B, to a narrow strip of icons (the app mark, New chat, Chats and the account), each named by a tooltip, and the page keeps its place beside it. In the strip the mark turns into the unfold button when it is hovered or focused, and the Chats icon unfolds it too. On a narrow screen the sidebar is a drawer, and only there does the page get a menu button at its top left to open it holds the app name, a "New chat" entry, then "Recents": the chats newest first, one line each (the first message, or "New chat"; the time it was started is a hover title), and at the bottom who is signed in. That row is a button: clicking it opens a menu above it with the account label and Log out, closed with Escape and reachable from the keyboard. Only things that work are in the menu: Memories (which the sidebar also leads to) and Log out. The open chat is marked. A new chat is not created until its first message is sent, so empty chats do not pile up: sending creates the session, sends the message and moves to `/chats/{id}`. One gap remains: if the server then refuses that first message (no model key, say), the empty chat already exists and stays in the list as "New chat"; there is no way to delete a chat yet.

**New chat.** A greeting centred in the page over a large rounded composer with the send button inside it, and three suggestions under it ("What do you remember about me?" and two more). A suggestion only fills the box, so the owner decides what is sent.

**Chat view.** The history, a composer and the reply streaming under the last message. The owner's messages sit right in a light bubble; a reply is plain text on the page, and the composer is the same rounded box, pinned to the bottom.

- Enter sends and Shift+Enter makes a new line (Enter while composing characters with an input method confirms them and does not send). While a reply is coming, Send is locked but the box stays free, so the next message can be written meanwhile; a message the server will not take stays in the box with the reason, and is cleared only once the server has taken it.
- A character count appears near the 4,000-character limit and the send button stops there. The server still decides, and a `413` is handled all the same.
- The reply is Markdown (the model writes `**bold**` and lists), shown as text elements only, never as raw HTML, and styled with Tailwind's typography classes.
- Scrolling follows the reply only while the owner is already at the bottom.
- The streaming reply sits in a live region, so a screen reader hears it arrive.

**Recalled memories.** Under each reply a line says how many memories were recalled for it and opens a list: the kind, the text, and for a past one the label "was true" (a memory replaced since a reply shows "since replaced", and a deleted one only that it was deleted) as a label. It is labelled "recalled" and not "used", on purpose: recall puts a memory in the prompt and cannot tell which of them the model relied on, which is also why `last_used_at` means "shown" (see `PHASE3.md`). The `memory` event arrives before the first word, so the list is there while the reply is still streaming. A reply that had no memories says "No memory matched", which is what the agent was told too.

**Memory list.** Tabs for active (the default), replaced, deleted and all, and a box that filters what is loaded, one box for every tab. A tab asks the server for its first 100 memories when it is first shown and keeps them, so going back to a tab does not ask again, and "Load more" asks for the next 100 until a page comes back short. Each memory is a row: its kind, its text, and under it an `external` badge when the source is not the owner, a `replaced` or `deleted` badge when it is one, and a line of "Created …", "Holds from/until …" when it does not always hold, "Last shown …" or "Never shown", and "Replaced by memory n" for a replaced one. A deleted memory is shown, softened, under its own tab, where the owner asked to see it; the old replies in a chat still hide its text. "Last shown" and not "last used", for the reason given under recalled memories. Days are shown in UTC, which is how the server keeps them: a date such as "4 January 2027" is stored as that day's midnight UTC, and in a time zone west of Greenwich it would read as the 3rd. A list of rows and not a table, so it reads on a phone. There are no edit or delete controls yet.

## Look and components

Tailwind CSS (v4, as `create-next-app` sets it up) with shadcn/ui's default style and the neutral base colour, lucide for icons. The theme is the CSS variables shadcn defines; light and dark follow the system setting through the `dark` class, with no toggle. A small script (`next/script`, run before hydration) sets the class before the first paint, so a dark system never flashes white, and a client component keeps it in step with the system afterwards. What each screen uses:

| Screen | shadcn components |
| --- | --- |
| Login | Card, Input, Label, Button, Alert |
| Sidebar and chat list | Sidebar (which brings Sheet for the drawer, Tooltip for the folded icons, and Skeleton), Avatar and DropdownMenu for the account menu, Button |
| Chat view | Textarea and Button for the composer, Skeleton while the history loads, Alert for errors; the conversation scrolls in a plain element, because following the reply needs its scroll position |
| Recalled memories | Collapsible, Badge for the kind, "was true" and "since replaced" |
| Memory list | Tabs, Badge for the kind, `external` and the status, Input for the filter, Button for "load more", Skeleton while it loads |
| App shell | Sidebar's provider, inset and trigger |

Only the components in this table are added. Anything custom is built from them, with `cn` merging the classes, and not from a second styling approach.

## Streaming

The reader takes the response body and yields events. Frames end at a blank line and each has an `event` and a `data` line (see the table in `PHASE3.md`); the reader copes with what a network does to that:

- a frame split across two chunks, or a multi-byte character split across them, so bytes are decoded as a stream and not chunk by chunk;
- `\r\n` line endings, comment lines and an event it does not know, which are skipped;
- a stream that ends without `done` or `error`. That is a lost connection, shown as such, and the saved turn is looked up by reloading the chat.

Closing the tab or navigating away does not stop the turn on the server: the answer is still stored and the chat is given back. Coming back shows it. If the owner returns while the turn is still running, the chat shows their question with no answer, and sending another message is refused with `409 turn_in_progress`; the view says the chat is still answering and reloads the history once after a few seconds. It does not poll.

**Where the reply is read.** Not by the page that sent the message. A small store outside React, keyed by chat id, sends the message, reads the stream and applies each event with the pure functions in `chat-state.ts`; the pages read it with `useSyncExternalStore`. That is what lets a new chat move to `/chats/{id}` while its first reply is still arriving, and lets the owner open another chat and come back without losing a reply in flight. When a turn ends (`done`, `error` or a lost connection) the history is read again, so the browser's guess is replaced by what the server stored: the real ids, and each memory's status as it stands now. A second message sent before that read returns is not overwritten by it. Signing out, or a refused token, empties the store, so the next owner never sees this one's text.

A failed turn leaves the owner's message saved with no reply. Sending the text again stores it a second time, and the model sees both. That is accepted for now: the view says "not answered, send it again", and a proper retry that does not repeat the message is not part of this phase.

## Signing in

The token is kept in `sessionStorage`: it survives a reload and is gone when the tab closes, and it is not shared with other tabs. It lasts 12 hours on the server and the client also stores when it ends, so it asks for the password again without a failed request first. Any `401` clears the token and goes to login, remembering where the visitor was. The guard is client-side: the signed-in pages render nothing until the token has been checked, then send a visitor without one to `/login`. Next's proxy (called middleware before this version) cannot see `sessionStorage`, so it is not used for this; the API is what actually refuses a request without a valid token. The guard has three answers, not two: the server and the render that hydrates what it sent cannot read `sessionStorage`, so for them the token is *not known*, which is neither signed in nor signed out. Treating it as signed out sent an owner who reloaded a page to login, and the login page, seeing the token, sent them straight back.

**The guard hides a page; it does not stop it being sent.** Next ships what Server Components render under a client component in its page data (`self.__next_f`), even when the client component renders nothing. So nothing in the signed-in group renders or fetches private data on the server: every page there is a client component that gets its data from the API with the token, in the browser, and the server-rendered part is static text only. Checked on the production build: the markup for a visitor is empty. For the same reason Next's dev-time check that a page navigates instantly cannot see these pages (it reports "Could not validate that a segment in your UI has instant navigation"), so each page in the group exports `instant = false`, a client-side transition all the same, and a test fails for any page that forgets it. Logging out clears it. Nothing in the bundle is secret: there is no API key in the browser and the model is only ever called from the server.

Because the answer is model output built from stored text, it is treated as untrusted: Markdown is rendered without raw HTML, links are shown only for `http` and `https` and open with `rel="noopener noreferrer"`, and a memory's text is always plain text.

## Errors and limits

| What happens | What the owner sees |
| --- | --- |
| Wrong password (`401`) | "That password is not right", the field kept |
| Login not configured (`503`) | "Login is not available: the server is not set up" |
| Token refused or expired (`401`) | Back to login, then back to where they were |
| `empty_message`, `message_too_long` | Prevented in the composer; if the server refuses anyway, its message |
| `chat_full` (409) | "This chat is full. Start a new one", with a button |
| `turn_in_progress` (409) | "Still answering the last message", then one reload of the history |
| `session_not_found` (404) | Back to the chat list |
| `error` event: `recall_failed`, `model_error`, `step_limit`, `history_unreadable`, `internal_error` | The server's plain message under the question, "not answered, send it again" |
| The stream ends with no `done` | "The connection was lost. Reload to see whether the reply was saved" |
| The agent is not configured (`503` on send) | "The assistant is not available: the server has no model key" |
| Network error | "Cannot reach the server" |

The 4,000-character limit is a constant in the frontend that matches the server's default; the server stays the authority, so a different `MAX_MESSAGE_CHARS` only costs a `413` that is handled as above.

## Tests

The frontend tests run in Vitest with Testing Library and a fake `fetch` that returns a real `ReadableStream`; no test calls the API or a model. The stream fixture is a turn recorded from the real API in phase 3, so the shapes are the real ones.

- **Reader:** a frame split across chunks, a split multi-byte character, `\r\n`, comments, an unknown event, a final frame without its blank line, a stream that ends without `done`.
- **Client:** the token is sent, a `401` signs out, an error body becomes its code, a network failure becomes a message.
- **Guard and redirects:** a visitor without a token, or with an expired one, sees nothing and is sent to login with a `next` that includes the query; a reload (which hydrates server HTML) sends a signed-in owner nowhere and a visitor to login once; a token refused later does the same from wherever the owner is; the handler is removed when the page goes; `safeNext` is tested against the addresses above, and what `loginUrl` writes, `safeNext` reads back.
- **The store and the chat view:** the question appears at once, then the memories, then the reply as it streams; `done` makes it a message with the stored id and its memories; an error drops the half reply and keeps the question; a refused message leaves the chat as it was and keeps the text in the box; one reply at a time; a reply keeps arriving with nobody watching; a history read does not wipe a newer message; the busy-chat reload happens once; the view follows the reply only while the owner is at the bottom; a missing chat goes home; signing out empties it all.
- **Views** (queried by role and label, never by class name; Radix needs `ResizeObserver`, pointer capture and `scrollIntoView` stubbed once in the test setup under jsdom): login (right and wrong password, `503`, return to the page asked for); the chat view (the memory list appears before the text, text is appended, `done` attaches the stored id, an `error` event shows its message, `409` and `413` are mapped, Enter and Shift+Enter, the composer is locked during a reply, the counter and the limit); a new chat is created on the first send and not before; the chat list; the memory list (a status tab asks for that status, load more, the filter, no edit controls).
- **Safety:** a `<script>` in a reply is shown as text, a `javascript:` link is not a link, a memory's text is not rendered as Markdown.
- **Backend** (`pytest`, no key): the ids are stored on the reply that ends a turn and on no other, old rows without them still load, the provider input does not contain them, `GET /sessions/{id}/messages` returns them resolved, a deleted memory comes back without its text, a replaced one with it, and a user message has an empty list.

The hooks run the type check, the linter and the tests when `frontend/` changed and `node_modules` is present, and say so when it is not.

## Run it

```sh
make up && make migrate
make seed                                         # the seed memories, needs OPENAI_API_KEY
make api                                          # the API on :8000; APP_ENV=dev in .env for /docs
make web                                          # npm install, then the dev server on :3000
```

Open `http://localhost:3000` and log in with the password whose hash is in `OWNER_PASSWORD_HASH`. Node 22 or later. `make web-test` runs the frontend tests and `make web-build` runs the production build (which also type-checks); `make web-lint` runs the type check and ESLint.

## Decisions to settle before starting

| Decision | Default if you say nothing |
| --- | --- |
| Stack | Next.js (App Router), React and TypeScript, as in `DESIGN.md`; Tailwind and shadcn/ui for the interface; Next's file routing for the pages; plain `fetch` and small hooks, no data library |
| What Next does here | App shell, routing, build and the `/api` rewrite. Client components only: no server rendering of data, no Route Handlers, no middleware auth, because the token is in the browser |
| How the browser reaches the API | Next rewrites `/api/*` to the backend, so one origin and no CORS setting. The rewrite needs a running Next server (`next start`), so a static export is not an option; where the built app runs is a deployment question for phase 9 |
| Where the token lives | `sessionStorage`, cleared on any `401` and on logout |
| Styling | Tailwind CSS v4 and shadcn/ui (Radix primitives, source copied into the repo), neutral base colour, lucide icons; light and dark follow the system through the `dark` class, no toggle |
| Markdown | Rendered with `react-markdown` without raw HTML; links limited to `http` and `https` |
| Recalled memories after a reload | Stored with the reply and returned with the chat (the alternative, showing them only while streaming, leaves a reloaded chat looking as if nothing was recalled) |
| A memory deleted since a reply | Shown as "a memory that has since been deleted", no text |
| Wording | "Recalled", not "used" |
| New chats | Created on the first message, not on the click |
| Layout | A sidebar with the recent chats, and a centred composer, as in the familiar chat apps; no images, plugins, voice or other tools the reference had |
| A turn still running after a reload | One reload of the history after a few seconds when a send is refused; no polling |
| Retrying a failed turn | Send the text again; a retry that does not store it twice is later |
| Where a reply is read | A store outside React, keyed by chat, so leaving the page never drops a reply in flight |
| The message box while a reply arrives | Stays editable, Send is locked |
| Tests | Vitest and Testing Library against a fake stream; the real-browser check is by hand, and an automated browser test is a phase 9 question |
| Hooks | Frontend checks run when `frontend/` changed and dependencies are installed |

## Gate check

| Check | Expected result |
| --- | --- |
| Login | A wrong password is refused with a message; the right one opens the chat; a reload stays signed in; a new tab asks again; logging out clears it |
| Streaming | A reply arrives in pieces in the browser, not all at once, through the rewrite, in the dev server and in a production build run with `next start` |
| Memories recalled | The list is under each reply while it streams and after a reload, the same on both; a past memory is labelled "was true"; a question with no match says so |
| History | A reloaded chat shows the same messages in the same order, and a chat from before this phase still opens |
| Forgotten and replaced | A memory deleted since shows no text on an old reply; a replaced one shows "since replaced" |
| Memory list | The tabs show the right statuses; a deleted memory appears only under `deleted` and `all`; load more pages through the list |
| Errors | A full chat, a chat still answering, an expired token, a stopped server and a refused message each give their message |
| A closed tab | Closing it mid-reply and coming back shows the saved answer |
| Safe rendering | A script tag and a `javascript:` link in a reply do nothing |
| Layout and keyboard | Usable at phone width (the chat list is a drawer), light and dark both readable, and the whole flow works with the keyboard alone: login, chat list, send, expand the memories, switch tabs, with a visible focus and a drawer that traps it |
| Tests | `make web-test` and `pytest` pass with no API key and no server |
| Phase 3 | The agent eval baseline is unchanged: the stored ids are not in the prompt |

## Not in this phase

- Editing and deleting memories from the list, and anything else that writes: phase 5
- Tools, confirmation prompts and the screens for them: phases 6 and 7
- Compacting a long chat: phase 8; until then the 100-message cap shows as "chat full"
- Tracing, the README demo and an automated browser test: phase 9
- A terminal client: phase 10
- Sign-up, several users and per-user data: phase 11
- A theme toggle, custom branding or a design system of our own beyond the shadcn defaults
- Deleting, renaming or searching chats, regenerating or editing a sent message, and attachments or voice
- Hosting: where `next start` runs, HTTPS and a production build pipeline
- A login limit or a token refresh: the token lasts 12 hours and then the password is asked for again
