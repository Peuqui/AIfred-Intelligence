# Browser Push Bus

> **Deutsche Version:** [browser-push-bus.md](../../de/architecture/browser-push-bus.md)

As of: 2026-05-22. Living document.

The **Browser Push Bus** is the Reflex-independent channel through which the
server updates the browser **without** going through Reflex state deltas.
Server side: [`browser_push()`](../../../aifred/lib/api/browser_bus.py) in
`aifred/lib/api/browser_bus.py`. Browser side: `browserEventSource` /
`startBrowserStream()` in [`assets/custom.js`](../../../assets/custom.js).

---

## The Problem

Reflex only pushes a state change to the browser as a delta if it
happens **inside an event handler** (`@rx.event`, or an
`@rx.event(background=True)` with `async with self:`).

A bare `asyncio.create_task(...)` runs **outside** this
system. It may mutate the server state — but Reflex does not notice
and sends nothing to the browser. The change only becomes visible when
some *other* event handler `yield`s later.

This affects everything AIfred deliberately offloads from the event handler
as a fire-and-forget task, so that the handler (and the user) does not have
to wait:

- **Streaming TTS finalize** — combines the sentence chunks into the replay WAV
  (`_finalize_streaming_tts_in_background`).
- **Session title generation** — a reasoning model needs
  >100 s for this; inline, that would block the handler **and the state lock**
  for that long (`_generate_session_title`).

Both patch server state that has to reach the browser — the bubble
audio URL, the sidebar title.

---

## The Solution

Instead of hoping for a Reflex delta, the background task **announces**
its result over an already open SSE connection. The browser
(`custom.js`) consumes the event and writes the DOM directly.

```
Background task ──browser_push(kind,…)──▶ _browser_event_storage + SSE queue
                                                       │
                                          GET /api/browser/stream/{sid}
                                                       │
                                          browserEventSource.onmessage
                                                       │
                                          switch(kind) ──▶ DOM patch
```

A single SSE stream per session carries **all** kinds — audio
(`tts`, `media`, …) and non-audio (`session_title`). A second
EventSource would be a second long-lived HTTP connection per tab; we
spare ourselves that.

### Server Side (`aifred/lib/api/browser_bus.py`)

| Building block | Purpose |
|---|---|
| `browser_push(session_id, kind, url, **meta)` | Put an event into storage + SSE queue |
| `_browser_event_storage` | Per session: `{queue, version, playback_rate}` |
| `_browser_sse_queues` | Per session: `asyncio.Queue` for the open SSE listener |
| `GET /api/browser/stream/{session_id}` | SSE endpoint, reconnect-safe via `Last-Event-ID` |
| `GET /api/browser/queue/{session_id}` | Polling fallback in case SSE is not available |
| `browser_queue_clear(session_id)` | Clear the queue (the version counter stays!) |

The **version** is strictly monotonic per session — it never decreases, not even
after `browser_queue_clear`. The client deduplicates SSE events via the
version; a reset would make the next `v1` look like an item
already seen.

### Browser Side (`assets/custom.js`)

`startBrowserStream(sessionId)` opens the `EventSource`. Important: the
call happens **within the user-gesture chain** (login, send button,
session switch) — this way every later `audio.play()` triggered by an
SSE event counts as user-initiated (no autoplay block).

`browserEventSource.onmessage` routes via `data.kind` to the matching
DOM patch.

---

## When to Use the Bus — and When Not

```
Does the code run in a Reflex event handler (@rx.event)?
├─ YES → normal state access, Reflex pushes the delta. NO bus needed.
└─ NO (bare create_task / background worker)
   │
   Does the code patch a single, clearly addressable DOM element?
   ├─ YES → Browser Push Bus: browser_push(...) + kind handler in custom.js.
   └─ NO (it is about a list / a lot of state)
      → Mutate the Reflex state and trust that an event pushes
        it. If the state backs an rx.foreach list filled by a
        background task: let the 500ms timer refresh_debug_console
        dirty-flag the list (see Pitfalls).
```

**Rule of thumb:** The bus is good when custom.js can patch *one* element
in a targeted way (an audio URL, a title text). For whole lists, the
Reflex `rx.foreach` path is right — otherwise the bus would have to bypass React
reconciliation, which becomes fragile (see Pitfalls, debug
console).

---

## Checklist: Adding a New `kind`

1. **Server — `aifred/lib/api/browser_bus.py`:**
   - Document the `kind` in the header comment of the bus block and in the docstring of
     `browser_push()`.
   - Does the `kind` need metadata beyond `url`? Then add an
     `elif kind == "…":` branch in `browser_push()` that fills `item[...]`
     (model: `media`, `seek`, `speed`). Otherwise nothing — unknown kinds
     cleanly get only `{kind, url, version, playback_rate}`.

2. **Sender — the background task:**
   - `from ..lib.api import browser_push` and call
     `browser_push(session_id, kind="…", url=…)`.
   - Mutate the server state **anyway** (`chat_history`, disk persistence,
     …). The bus push only bridges the gap until the next regular
     Reflex re-render — after that the state must be consistent, otherwise
     the re-render breaks the DOM patch again.

3. **Browser — `assets/custom.js`:**
   - In `browserEventSource.onmessage`, add an `if (kind === '…') { … return; }`
     — **after** the version dedup block.
   - Set `ttsQueueVersion = data.version;` (dedup shared across all kinds).
   - Keep the DOM patch idempotent: the event can arrive again on SSE reconnect
     (replay), and the next Reflex re-render overwrites
     the patch anyway.

4. **DOM anchor:** If the handler patches an element rendered by Reflex,
   it needs a stable selector — e.g. a `custom_attrs={"data-…": …}`
   on the Reflex component (model: `data-session-id` on the
   session list, `data-audio-urls` on the bubble buttons).

5. Checks: `node --check assets/custom.js`, plus `py_compile`/`ruff`/`mypy`.

---

## Current Kinds

| Kind | Payload | Purpose |
|---|---|---|
| `tts` | `{url, playback_rate}` | TTS chunk, gapless queue |
| `media` | `{url, state_key, start_pos_sec, is_stream, audio_type}` | Single track with position persistence |
| `stop` / `pause` / `resume` | — | Audio control |
| `seek` | `{position_sec, relative}` | Jump within the track |
| `speed` | `{factor}` | Set `audio.playbackRate` |
| `bubble_audio` | `{url}` | Combined replay URL on the finished chat bubble |
| `session_title` | `{url}` (= title text) | Generated title into the session list |

---

## Pitfalls

- **Idempotency:** On SSE reconnect, the server replays all items with
  `version > Last-Event-ID`. Every handler must tolerate the same event
  arriving a second time.
- **State consistency:** The bus does not replace the state mutation, it
  complements it. Whoever only pushes but does not mutate the state loses the
  DOM patch on the next re-render.
- **User gesture:** Audio kinds only work because the EventSource was opened in
  a user gesture. Therefore never call `startBrowserStream` for the first time
  from a timer/callback without a gesture context.
- **Debug console — NOT via the bus:** The console renders via
  `rx.foreach(debug_messages)`. Background tasks append lines to
  `debug_messages` but do not trigger a Reflex delta. Pushing them via the bus
  was rejected: custom.js would have to insert DOM into an
  `rx.foreach` container → React reconciliation conflicts
  (duplicated / wrongly ordered nodes). Instead, the 500ms timer
  `refresh_debug_console` explicitly flags the list dirty
  (`self.debug_messages = list(self.debug_messages)`) and `yield`s — this way
  Reflex pushes the background lines with at most 500 ms latency. **Lesson:** The
  bus is for single, precisely addressable DOM patches; a whole list rendered by
  Reflex is left to the Reflex path.
