# Browser Push Bus

> **Deutsche Version:** [browser-push-bus.md](../../de/architecture/browser-push-bus.md)

As of: 2026-11-18. Living document.

The **Browser Push Bus** is the channel through which the server updates the open tabs of a session
without them asking: speech output, music, the status line and the message "this list has changed".
It is not a stream of its own but a call onto the **event stream of the session**: server side
[`browser_push()`](../../../aifred/lib/browser_push.py), hub
[`session_events.py`](../../../aifred/lib/session_events.py), browser side `followSession()` in
[`frontend/src/api/events.ts`](../../../frontend/src/api/events.ts).

---

## The path of an event

```
any code ──browser_push(sid, kind, **fields)──▶ session_events.publish
(turn, background task,                                  │
 scheduler, channel thread)            hub per session: id counter, buffer, one queue per tab
                                                         │
                                       GET /api/sessions/{id}/events  (SSE)
                                                         │
                                       EventSource (followSession) ──▶ stores/chat.ts handle(kind, data)
                                                         │
                                       store, lib/speech.ts, lib/media.ts, status line
```

The caller does not have to sit in a handler or flag any state dirty: `browser_push()` calls
`session_events.publish`, that is all. A thread with a loop of its own (channels and the scheduler
run in the message hub's thread) may call it too: the hub belongs to the server's loop
(`bind_home_loop` at startup), `on_home_loop` hands the work over in the order of the calls, which is
why it needs no lock.

### Hub (`aifred/lib/session_events.py`)

| Part | Purpose |
|---|---|
| `publish(session_id, kind, data, replaces=…)` | event to every subscriber of the session and into the buffer; `replaces` names kinds whose earlier events leave the buffer |
| `subscribe(session_id, after_id)` | yields the missed events and the tab's queue; registering and reading the buffer happen without an `await` in between, so every event lands in exactly one of the two |
| `clear_replay(session_id, keep=…)` | empty the buffer (ids keep rising); a new turn calls it (`turn_service.py:297`) and keeps only the `LATEST_WINS_KINDS` |
| `missed_beyond_replay` | whether a client may have missed events the buffer no longer holds |
| `sessions_with_subscribers` | the sessions a tab listens to right now |

Each tab has its own queue, so two tabs of one session both see every event. The id rises strictly
per session and never falls; the browser remembers the last one and sends it on reconnect
(`Last-Event-ID`), the first call passes it as `?after=` (an `EventSource` cannot set headers). If the
id count began again after a server restart, or the buffer was emptied after the client's last id, the
stream begins with `resync` (`aifred/lib/api/sessions.py`, `sessions_events`) and the page loads the
session anew (`stores/chat.ts`, `resync()`). After 15 seconds without an event a keep-alive comment
goes out (`SSE_KEEPALIVE_S`).

### Kinds that carry a state

An event that reports a state instead of an occurrence replaces its predecessors in the buffer: a page
that reconnects needs the latest, not the history. These are `LATEST_WINS_KINDS` (`browser_push.py`):
`service_status` and, per list, `<name>_changed`. `turn_phase`, `tool_status` and
`speech_regenerating` replace themselves through `replaces` as well.

---

## What `browser_push()` sends

| Kind | Fields | Purpose |
|---|---|---|
| `tts` | `url` | audio file of the reading |
| `tts_speak` | `utterance {text, language, voice, rate, pitch}` | a part the browser's own voice speaks |
| `tts_pause` | `ms` | silence between two parts |
| `media` | `url, state_key, start_pos_sec, is_stream, audio_type, queue, pre_roll_sec, position_save_sec` | one track, its position saved; `queue` = the folder's further tracks `{audio_url, state_key}`, `pre_roll_sec` = jump back after speech output, `position_save_sec` = interval of the position saving (both `config.py`) |
| `stop` / `pause` / `resume` | – | steer the audio |
| `seek` | `position_sec, relative` | jump in the track |
| `speed` | `factor` | `playbackRate` (0.25 to 4.0) |
| `service_status` | snapshot (`ServiceSnapshot.as_json`) | status line (model, describer, STT, TTS), see [service-status.md](service-status.md) |
| `<name>_changed` | – | the list `<name>` has changed (`notify_changed`) |

The page reads them in `stores/chat.ts` (`handle`), `lib/speech.ts` (`tts*`) and `lib/media.ts`
(`media`, `pause`, `resume`, `seek`, `speed`). The hub also carries the events of the turn
(`turn_started`, `token`, `message_added`, `turn_finished` …, from `turn_service.py`), of dictation,
of the reading and of the message hub; the full list the browser listens to is `SESSION_EVENT_KINDS`
in `events.ts`.

### Lists change: `notify_changed`

`notify_changed(resource)` (`browser_push.py`) tells every connected page that a list is to be loaded
anew; `RESOURCES` are `jobs`, `sessions`, `agents`, `memory`, `plugins` (the browser's counterpart:
`RESOURCES` in `events.ts`). Callers are the saving of a session, the trash, agent configuration,
agent memory, the scheduler and the plugin manager. The page (`lib/resourceChanges.ts`) joins a run
of messages (a session is saved several times in a turn) into one load within 300 ms; the list's store
hooks in with `onResourceChanged`. `notify_changed` may come from any thread: the list of connected
sessions is read on the server's loop.

### Debug console

The console's lines travel the same stream as a `debug` event, at once when `debug()` is called
(`aifred/lib/debug_bus.py`). They reach the session file only once, at the end of the `session_scope`
(the file is rewritten as a whole, some are hundreds of KB). A page that opens in the middle of a turn
gets the waiting lines through `GET /api/sessions/{id}` (`debug_bus.pending_lines`), the rest from the
file.

---

## When to use the bus

Anything the server wants to report to open tabs on its own, and that is not part of the answer to a
request: audio, status changes, "a list has changed". The answer to a call stays the HTTP response.
Messages about lists carry no data, only the reason; the page reloads through the API, so the API
stays the single source.

---

## Checklist: add a new kind

1. **Server:** call `browser_push(session_id, "<kind>", field=…)` and name the kind and its fields in
   the docstring of `browser_push()`. If the kind reports a state, add it to `LATEST_WINS_KINDS`; a new
   list kind belongs in `RESOURCES` (then `<name>_changed` arises by itself).
2. **Browser:** enter the kind in `SESSION_EVENT_KINDS` (`events.ts`), otherwise the browser does not
   hear it, and add a `case` in `stores/chat.ts` (`handle`). A new list kind also goes into `RESOURCES`
   of `events.ts`, and the list's store calls `onResourceChanged`.
3. Keep the handler **idempotent**: on reconnect, events from the buffer arrive again.
4. Check: `npm run build` and `npx vitest run` in `frontend/`, `ruff`, `mypy`.

## Pitfalls

- **Idempotence:** On reconnect the server replays every event with `id > Last-Event-ID`.
- **Not in the buffer:** A client behind the last emptying of the buffer gets `resync` and reloads; a
  handler must not rely on every event arriving.
- **Autoplay:** The browser allows `audio.play()` only after a gesture on the page. `lib/speech.ts`
  plays a silent sound once on the first click or key press (`unlock`); after that the readings that
  arrive later over the stream may sound.
