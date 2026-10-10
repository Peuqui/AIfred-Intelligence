# Spoken Output — Escalation List, Unit, Announcements

> **Deutsche Version:** [tts-escalation.md](../../de/architecture/tts-escalation.md)

As of: 2026-10-10. Describes how AIfred decides who speaks and how speech reaches the output
paths (browser, FreeEcho.2). Code: [`aifred/lib/tts_escalation.py`](../../../aifred/lib/tts_escalation.py),
[`aifred/lib/tts_engines/`](../../../aifred/lib/tts_engines/),
[`aifred/lib/speech_synthesis.py`](../../../aifred/lib/speech_synthesis.py),
[`aifred/lib/api/tts.py`](../../../aifred/lib/api/tts.py),
[`aifred/lib/api/announce.py`](../../../aifred/lib/api/announce.py).

Related: VRAM planning and the menu in [tts-vram-workflow.md](tts-vram-workflow.md), the
structure of an engine in [tts-container-conventions.md](tts-container-conventions.md), the
cloud engine in [dashscope-voice-cloning.md](dashscope-voice-cloning.md).

---

## Escalation List

Which engine speaks on which machine is kept in **one global list** — there is no fixed engine
choice any more. The browser, channels with their own speaker (FreeEcho.2) and the narrator read
the same list. It lives in `settings.json`:

| Key | Content |
|-----|---------|
| `tts_escalation` | ordered entries `{"engine", "host", "enabled"}`; `host: null` = this machine or cloud |
| `tts_hosts` | other machines with TTS containers: `name`, `address`, `enabled`, `ssh` (`user@host:port`, empty = AIfred controls nothing), optional `ports` per engine |

**Order:** top to bottom; the first entry that can speak right now speaks (`SpeechRun.entry()`).
**On/off:** `enabled` per entry; a disabled entry is skipped. A host can be switched off as a
whole (`tts_hosts[].enabled`): its entries are skipped and its containers stopped. Order, on/off,
hosts and entries are edited in the spoken-output menu (`_edit_tts_lists()` in
`_tts_config_mixin.py`). A broken list (unknown engine, unknown host) raises `ValueError` and is
not silently ignored.

### Default list

The list of a fresh install is derived from the engines (`in_default_escalation`, sorted by
`display_order`; `config.default_tts_escalation()`):

| Position | Engine (key) | `display_order` |
|----------|--------------|-----------------|
| 1 | Qwen3-TTS (`qwen3local`) | 10 |
| 2 | XTTS (`xtts`) | 20 |
| 3 | DashScope Qwen-Audio 3 (`dashscope_audio3`) | 50 |
| 4 | Piper (`piper`) | 60 |
| 5 | Edge (`edge`) | 80 |
| 6 | Browser (`browser`) | 90 |

Fish-Speech (30), MOSS (40) and eSpeak (70) exist as engines but are not in the default list.

### When an entry speaks

`_skip_reason()` checks in this order; the first condition that applies skips the entry (debug
line `TTS escalation: skip <entry> (<reason>)`):

1. Entry disabled.
2. Browser entry, but the reply does not go into a browser session.
3. Local engine whose Docker image is not built.
4. The entry's host is switched off.
5. Engine runs (`is_running()`) → it speaks.
6. Remote container sleeps: with an SSH target at the host AIfred starts it
   (`scripts/tts-host-ctl.sh start <service>`) and waits up to `startup_timeout_s`; without an
   SSH target the entry fails.
7. Local engine without GPU: "not available" (e.g. cloud without an API key).
8. Local GPU engine: start on a fitting card (next section).

**The main model is never reloaded for TTS.** If the local TTS does not fit, the list silently
moves on to the next entry.

### Placement on the cards

`place_local_gpu_engine()` (one rule for the list and the start API): the engine's measured
burn-in peak (`tts_vram_cache`) plus `LLAMACPP_TTS_BURNIN_HEADROOM_MB` (512 MB) must fit into
the **free** memory of a card. The side-channel card (`pick_tts_gpu()`) is preferred, else the
fitting card with the most free memory. Without a measured value, without a GPU listing, or when
no card fits, the placement refuses (`PlacementRefused`) and the entry fails. Local GPU starts
run one after another (one lock) so a second start sees the memory the first one is just taking.
If a main model later needs the card, the backends' GPU guard clears the container; the engine a
`-tts-<engine>` profile keeps room for on the side-channel card is exempt (`planned_tts_engine()`).

### Failure and voice change

Every engine raises `TTSFailure` with a reason (`unreachable`, `engine`, `software`) on error.
`SpeechRun` (one reply or announcement):

- Failure **before the first sound**: silently to the next entry (debug line only).
- Failure **mid-text**: the successor first announces the voice change with its reason
  (`tts_switch_*`), then speaks the unfinished sentence.
- A failed entry stays out for the rest of the run.
- If an entry has no voice for the agent (`resolve_voice()`), it fails.
- List exhausted: `NoSpeechAvailable`; a channel with a speaker then stays silent (logged loudly).

Simultaneous syntheses per engine and machine are limited to `max_parallel_requests`.

### Status of the entries

`entry_status()` returns a key for the status column of the list editor (`tts_status_<key>` in
`lib/i18n`) without starting anything: `disabled`, `host_off`, `no_image`, `starting`, `running`
(container runs), `cloud` (cloud ready), `ready` (process without a port ready), `start_failed`,
`no_access` (cloud without access), `unreachable` (host without SSH target does not answer),
`sleeping` (starts on demand).

---

## Unit of Spoken Output

How much text goes to the engine at once is **one setting per TTS engine**, system-wide for all
agents and channels:

| Unit | Meaning |
|------|---------|
| `sentence` | sentence by sentence — earliest first speech; further sentences are generated meanwhile |
| `paragraph` | paragraph by paragraph (at blank lines) — often better intonation |
| `whole` | everything in one piece — best quality, the first speech comes only after complete generation |

- Stored in `tts_toggles_per_engine[engine]["unit"]` (spoken-output menu); the default per engine
  is `default_speech_unit` (`sentence`, `whole` for MOSS, Piper and eSpeak).
- **SSOT for reading:** `speech_unit_for(engine_key)` in
  [`tts_engines/registry.py`](../../../aifred/lib/tts_engines/registry.py) — the browser and every
  channel call it; no channel has a setting of its own. An invalid value raises `ValueError`.
- **Splitting:** `build_speech_segments()` in `audio_processing.py` turns the paragraphs of an
  announcement into texts and silence (`pause_ms`) according to the unit; the sentence split itself
  is `split_text_for_streaming_tts()`.
- **Browser streaming** is not a switch of its own: `tts_speech_unit` is the unit of the topmost
  enabled entry (the one that really speaks is only known at the first sentence); "streaming"
  means every unit except `whole`.

---

## Browser Speech Engine

`browser` (Web Speech API, `speechSynthesis`) is an engine of the list that produces **no audio
file**: `SpeechRun.speak_in_browser()` returns a `BrowserUtterance` (text, language, voice, rate,
pitch) instead of a URL, which the page speaks with a voice of the device.

- It only speaks for replies into an open browser session (`in_browser=True`); it is skipped for
  the Echo, the narrator and re-synthesis.
- It comes last in the default list (`display_order` 90): the server cannot know whether the
  device has a voice, so no engine could take over after it.
- Voice `Auto` = the device picks one for the language (`default_voice`); rate and pitch are
  handled by `speechSynthesis` itself, no ffmpeg.
- Place label in the list: "Device" (`location_label()`).

---

## TTS Start API and Control Page

[`aifred/lib/api/tts.py`](../../../aifred/lib/api/tts.py) makes the list usable for callers
without an LLM — meant for the service control page, which shows the list and starts or stops
containers. The card and host rules are the same as for AIfred's own speech (same start path
`launch_entry()`).

Auth: `Authorization: Bearer <TTS_CONTROL_API_TOKEN>`; without a valid token the call is rejected
(`require_service_token`). The caller name (`caller`) is required and used for display only.

| Endpoint | Effect |
|----------|--------|
| `GET /tts/entries?lang=de\|en` | The list in speaking order with live status: `engine`, `host`, `label` ("engine · place"), `enabled`, `status`, `status_text`, `controllable`, `container` (local container name), `detail` (why the last start failed) |
| `POST /tts/start` `{engine, host, caller}` | Starts the container in the background (model loading takes a minute or more); `/tts/entries` shows `starting`, then `running` or `start_failed`. Locally on the card AIfred picks, on a host over SSH. 404 = no such entry, 409 = not controllable or start not possible |
| `POST /tts/stop` `{engine, host, caller}` | Stops the container, VRAM free. 404/409 as above |

`host: null` = this machine. "Controllable" (`is_controllable()`) are local GPU containers and
containers on a host with an SSH target. If a start of the entry is already running, `start`
answers "already starting".

---

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
  (`FREEECHO2_NOTIFICATION_START_TONE` / `_END_TONE`), not the caller's business; which sound it
  is, the puck decides. If music, an audiobook or speech is playing, the server pauses it at the
  puck before the announcement.
- Which engine speaks is decided by the escalation list; the unit is `speech_unit_for()` of the
  speaking engine.

### `announce_into_rooms`

`announce_into_rooms(channel, rooms, text, speaker, metadata)` in `message_processor.py` is the
**one place** for an announcement made out loud (the agent tool `freeecho2_announce` and the
announce API use it). Per room:

1. the bubble first: `record_autonomous_turn` adds an assistant turn to the room's session with
   `speaker` as sender,
2. then the announcement into that session (`announce_to_channel`), so what is spoken stays on
   that bubble. In the opposite order a short announcement would be spoken before its bubble
   exists.

Returns the rooms reached. A room not reached keeps its unspoken bubble; that is logged, not
hidden.

---

## Echo Speech and the Bubble

[`speech_synthesis.py`](../../../aifred/lib/speech_synthesis.py) is the SSOT of speech generation
for channels with their own speaker: text → 48 kHz PCM (mono, 16 bit) in a growing `TTSBuffer`.
The channel only hands the PCM to its device.

- `start_speech_stream()` generates the **first sentence** and returns the buffer; a background
  task generates the remaining segments (texts and silence in ms) and appends them while the
  device is already playing. Beforehand `clean_text_for_tts()` cleans the text (Markdown, emojis,
  code, links) — the same function as in the browser chat.
- Which engine speaks is decided by the escalation list (`SpeechRun`); the **language** is given
  by the caller (`run.language`); the **granularity** of the split is the engine's unit
  (`speech_unit_for`). If an engine fails mid-stream, the next entry takes over; when all are
  exhausted the stream ends without an end tone (logged loudly).
- **Bubble:** once everything is generated (also for a stream that broke off: what was spoken until
  then), `keep_spoken_audio()` joins the sentence audio files into the session's audio and attaches
  it to the reply bubble together with the note on who spoke (`SpeechRun.note(run.language)`, with
  the reason of a switch, in the language of the run) — like a browser reply. Without a session
  (a tool announcement with no bubble of its own) nothing is stored.
