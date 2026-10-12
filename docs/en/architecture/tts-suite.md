# Spoken Output — TTS-Suite, List, Unit, Announcements

> **Deutsche Version:** [tts-suite.md](../../de/architecture/tts-suite.md)

As of: 2026-10-12. AIfred no longer synthesizes speech itself. Engines, containers, the choice
of the card, the escalation to the next engine and the voice-change announcement belong to the
**TTS-Suite**, a separate service (repository
[Peuqui/TTS-Suite](https://github.com/Peuqui/TTS-Suite), contract in its `docs/INTERFACE.md`).
AIfred is its client: it sends text and its own list, and receives audio as a stream of pieces.

Code: [`tts_client.py`](../../../aifred/lib/tts_client.py) (HTTP + WebSocket),
[`tts_service.py`](../../../aifred/lib/tts_service.py) (adapter: the service's catalog ↔ AIfred's list),
[`tts_voices.py`](../../../aifred/lib/tts_voices.py),
[`browser_reading.py`](../../../aifred/lib/browser_reading.py),
[`speech_synthesis.py`](../../../aifred/lib/speech_synthesis.py),
[`tts_llm_coordination.py`](../../../aifred/lib/tts_llm_coordination.py),
[`tts_stress_burnin.py`](../../../aifred/lib/tts_stress_burnin.py),
[`api/tts.py`](../../../aifred/lib/api/tts.py), [`api/announce.py`](../../../aifred/lib/api/announce.py).

Related: the cloud engine in [dashscope-voice-cloning.md](dashscope-voice-cloning.md), VRAM
planning in the [calibration documents](calibration-strategy.md).

---

## The Service

- Runs as the system unit `tts-suite@<user>.service` on `127.0.0.1:5090` (`TTS_SUITE_URL`). If it
  is not running, `TTSClient.ensure_ready()` starts the unit (polkit rule) and waits for
  `/health`.
- Reading the catalog, speaking and releasing engines need no token; AIfred sends none. The
  configuration of the service (hosts, GPU rules, pronunciations, voices) is edited in the
  service's own web interface.
- AIfred **never** starts or stops a container itself. It asks the service to release engines
  (`POST /release`, see *VRAM*); everything else — start on demand, unload when idle — is the
  service's own business.
- Other machines with engines (e.g. Aragon) are hosts of the **service's** configuration
  (`~/.config/tts-suite/config.json`), not of AIfred's settings. `tts_hosts` in `settings.json`
  is obsolete.

## AIfred's List

Which engines speak, in which order, and in which unit is kept in **one global list** in
`settings.json`, read by the browser, the Echo, announcements and the narrator:

| Key | Content |
|-----|---------|
| `tts_escalation` | ordered entries `{"engine", "host", "enabled"}`; `host: null` = this machine or cloud |
| `tts_toggles_per_engine[engine]["unit"]` | the unit of the engine (see below); empty = the engine's recommendation |
| `tts_agent_voices_per_engine` | per engine and agent: `voice`, `speed`, `pitch` |
| `enable_tts`, `tts_autoplay`, `tts_agents` | spoken output on/off, Auto-Play, per-agent on/off |

AIfred **reads and switches** the list; it removes no entry and defines no engine. The catalog of
what exists comes from the service (`GET /engines`, cached for a short time): every engine of
this machine and of each configured host, with status, label, recommended unit, `installed`
(can be started) and the VRAM it holds. A catalog row the list does not know yet is appended
switched off. The editor (settings → TTS, agent editor) shows order, on/off and unit per row with
the live status ("runs", "sleeps — starts on demand", "cloud ready", "not reachable", …).

The default list of a fresh install (`DEFAULT_TTS_ESCALATION`): Qwen3-TTS, XTTS, DashScope
Qwen-Audio 3, Piper, Edge, Browser.

## What a Reading Sends and Gets

A reading is one `POST /speak` (whole text) or one WebSocket `/speak/stream` (text in pieces
while the LLM writes). The request carries:

- `engines`: AIfred's active entries in order, each `{engine, unit, host}` — **the list of this
  request**; the service decides, top to bottom, which entry speaks now.
- `voices`: `{engine: {voice, speed, pitch}}` of the speaking agent per engine
  (`voices_for()`; an engine without a configured voice is left out and the service then uses
  its own default or reports it in the trace).
- `run`: an id per reply (`new_run_id()`), the same for all pieces of that reply. The service
  remembers per run which engines failed, whether a code block is open across two pieces and
  whether a change of voice was already announced.
- `language`, `paragraph_pause_ms`.

The answer is an ordered stream of **segments** (audio, or a keep-alive / pause marker) and at
the end a **summary**: the `trace` (per entry `spoke` / `failed` / `skipped` with the reason) and
`switch` (set if a voice change was announced).

**Escalation and voice change** are the service's: an entry that fails before the first sound is
skipped silently, one that fails mid-text is followed by the next, which first announces the
change with its reason ("Stimmwechsel wegen Serverausfall, weiter mit XTTS") exactly once and then
speaks on. AIfred shows the outcome: the debug lines `TTS escalation: <entry> speaks / failed …
/ voice change announced` (in `aifred_debug.log` and the console) and the note on the reply bubble
("Qwen3-TTS · Aragon", with the reason of a switch).

**Text cleaning:** the service cleans Markdown, code, tables, LaTeX, URLs, emojis and
pronunciations (`AIfred` → "Alfred") itself. AIfred only strips what is its own before sending:
`<think>`, `<details>`, `<vlm_output>`, `<data>` blocks, the performance footer, round labels,
parenthesised times (`tts_text_filter.py`).

## Unit of Spoken Output

How much text goes to the engine at once is **one setting per TTS engine**, system-wide:

| Unit | Meaning |
|------|---------|
| `sentence` | sentence by sentence — earliest first speech; the next sentences are generated meanwhile |
| `paragraph` | paragraph by paragraph — often better intonation |
| `whole` | everything in one piece — best quality, the first speech comes only after complete generation |

- The user's choice is `tts_toggles_per_engine[engine]["unit"]`; without one the service's
  recommendation (`unit_default` of the catalog row) applies. **SSOT for reading:**
  `speech_unit_for(engine_key)` in `tts_service.py`.
- The **service** splits the text by the unit of the speaking engine (sentences with look-ahead:
  the first sentence goes out at once, from the second on the service waits for the next one; very
  short pieces join the next); AIfred does no splitting.
- **Browser streaming** is the unit of the topmost enabled entry; "streaming" means every unit
  except `whole`.

## Speech Paths

- **Browser** (`browser_reading.py`): a `BrowserReading` per reply opens the WebSocket, feeds the
  text while the LLM writes and pushes each segment over the push bus (`tts`, `tts_pause`) to the
  page's player. The reading is opened where the text starts to flow (`_stream_agent_to_history`
  per agent, `_process_vision_request` for VL Direct); a still open reading is ended when a new
  one starts. Afterwards the segments are joined into the session's audio and attached to the
  bubble with the note (replay, export).
- **Browser voice** (`browser`, Web Speech API): an entry of the list that produces no file. The
  service never speaks with it; the entries **above** it go to the service, the browser's own
  voice speaks if none of them can (or if it is the topmost entry). An entry below it is never
  reached. This path needs no service: with the service down the browser speaks.
- **Echo / announcements / replay** (`speech_synthesis.py`): text → 48 kHz PCM (mono, 16 bit)
  in a growing buffer the device plays while the rest is still being generated; the audio is
  kept on the bubble like a browser reply.
- **Narrator** (`narrate_file`): reads a whole file with **one** engine from start to end (no voice
  change mid-book), one run id per file; the files are joined with ffmpeg.

## VRAM: Reserve, Release, Burn-in

- **Where an engine runs does not matter to AIfred.** The calibration reserves room: the
  profile `<model>-tts-<engine>` of llama-swap/vLLM leaves room for that engine (its measured
  peak plus headroom), and the service puts the engine on a card with room by itself.
- **Plan:** which `-tts-<engine>` profile loads is read from `settings.json` and the llama-swap
  configuration alone (`planned_tts_engine()`: the topmost active local entry for which the model
  has a calibrated profile). The service is not asked, so a service that is down does not stop
  the language model.
- **Before a cold start of the main model** AIfred asks the service to release **every local GPU
  engine** (`release_gpu_engines()`, SSOT for everything that needs room: model load, describer,
  calibration). The service decides per target; a container a reading holds is refused (`in_use`),
  which is logged, and the load goes on as with too little room. After the load the next reading
  starts an engine again on a card with room (Qwen3 about 18 s, XTTS about 33 s to the first sound).
- **Burn-in** (`tts_stress_burnin.py`): the calibration has the service read a worst-case text
  on the engine and takes the peak of the VRAM the catalog reports; the result is cached in
  `data/tts_vram_cache.json`.

## When the Service Is Not Reachable

TTS paths fail **loudly**: a reading reports the reason (toast, debug line); the browser voice
takes over if its entry is switched on. The rest of AIfred keeps working: planning of the LLM
profile reads settings only, the settings editor shows the reason instead of the list, calibration
shows no TTS column, the vision check reports "room unknown".
The client is strict on purpose: fields it does not know are an `IncompatibleService` error
naming them (service and client were not updated together).

## Read-only List API and Control Page

`GET /tts/entries?lang=de|en` ([`api/tts.py`](../../../aifred/lib/api/tts.py)) returns AIfred's
list in speaking order with the live status of each entry (`engine`, `host`, `label`, `enabled`,
`status`, `status_text`, `detail`). Auth: `Authorization: Bearer <TTS_CONTROL_API_TOKEN>`. There is
no start or stop endpoint: engines are started by the service on demand and by hand in the
service's own web interface.

## Announcement Without an LLM: `POST /api/audio/announce`

[`aifred/lib/api/announce.py`](../../../aifred/lib/api/announce.py) reads a finished text aloud in
a FreeEcho.2 room (caller e.g. Agent-Orc). Same delivery path as the proactive alerts:
`announce_to_channel` → TTS of the Echo plugin → alert queue; announcements of one room play one
after the other and a running announcement is not interrupted.

- **Auth:** `Authorization: Bearer <ANNOUNCE_API_TOKEN>`, rejected without a token.
- **Body:** `{"room", "text"}` **or** `{"room", "texts": [...], "pause_ms"}` (exactly one of
  `text`/`texts`), plus `speaker` (required, the caller's name, display only — it appears in the
  bubble header). `room` = room name, `@group` or `*`. `texts` = several paragraphs as **one**
  announcement; `pause_ms` = silence between the paragraphs (default `ANNOUNCE_PAUSE_MS` = 1,000 ms,
  at most `ANNOUNCE_MAX_PAUSE_MS` = 5,000 ms).
- **Limits:** 413 above `ANNOUNCE_MAX_CHARS` (1,200) characters per entry or
  `ANNOUNCE_MAX_TOTAL_CHARS` (4,000) in total — no silent truncation; 404 for an unknown or not
  connected room; 502 if the announcement could not be delivered.
- **Rooms:** `GET /api/audio/announce/rooms` returns the rooms connected right now.
- **Tones:** the chime is fixed to `notification`, never `alarm`. Whether a start tone before and
  an end tone after the announcement are requested is a plugin setting
  (`FREEECHO2_NOTIFICATION_START_TONE` / `_END_TONE`), not the caller's business. If music, an
  audiobook or speech is playing, the server pauses it at the puck before the announcement.
- Which engine speaks is decided by the list and the service; the unit is `speech_unit_for()` of
  the speaking engine.

### `announce_into_rooms`

`announce_into_rooms(channel, rooms, text, speaker, metadata)` in `message_processor.py` is the
**one place** for an announcement made out loud (the agent tool `freeecho2_announce` and the
announce API use it). Per room: first the bubble (`record_autonomous_turn` adds an assistant turn
with `speaker` as sender), then the announcement into that session (`announce_to_channel`), so what
is spoken stays on that bubble. Returns the rooms reached; a room not reached keeps its unspoken
bubble, which is logged, not hidden.
