# Proactive Alert Pipeline

> **Deutsche Version:** [proactive-alerts.md](../../de/architecture/proactive-alerts.md)

Status: **in progress** (package 1 = core). First vertical slice:
unknown face at an armed camera → Telegram with image.

AIfred should speak up on its own when something important happens —
not only when asked. Instead of a vision-specific "notifier", we
build a **generic, pluggable pipeline**: vision is just the first
*producer*, Telegram just the first *sink*. If the core is agnostic,
each additional producer/channel costs almost nothing.

## Architecture in one sentence

Any **producers** emit a neutral **`AlertEvent`** to a
central **dispatcher**; a **rule engine** decides (matching,
throttling) which **sinks** (channel plugins) it goes to; the sinks deliver
proactively. The core knows nothing concrete — producers and sinks plug in.

```
Producer (vision, system, scheduler, …)
        │  emit(AlertEvent)
        ▼
   Dispatcher  ──►  Rule engine  ──►  Throttle (dedup + cooldown + quiet hours)
        │
        ▼
   Sinks (channel plugins: telegram, discord, email, freeecho-voice, …)
```

## The fixed core (`aifred/lib/alert_bus.py`)

Stays small forever and producer/channel-agnostic.

- **`AlertEvent`** — neutral dataclass:
  - `producer` (e.g. `"vision"`), `category` (`"face_unknown"`),
    `source_id` (`"cam/office"`), `severity` (`"info" | "warning" | "critical"`),
  - `title`, `body` (display text),
  - `dedup_key` (throttle axis, see below),
  - `media` (optional image path/URL),
  - `timestamp`, `metadata`.
  - No producer-specific fields in the core.
- **`AlertRule`** — `producer`, optional filters (`category`, `source_id`,
  `min_severity`), `sinks` (list of channel names), `min_interval_sec`,
  optional `quiet_hours`. References only *registered* producers/sinks.
- **`AlertDispatcher`** — `emit(event)`: find matching rules → throttle →
  hand over to a `deliver` function per rule (injectable for tests,
  default = `_default_deliver`). Throttle state: `(rule, dedup_key) → last
  sent`. The dispatcher core (matching + throttle) stays pure and doesn't
  deliver anything itself — that is done by the SSoT delivery (see below).

Gating such as `armed` is **producer-specific** and stays in the
producer (vision only emits when armed) — the core stays agnostic.

## Pluggable roles

1. **Producer** (new) — registers with a manifest (`name`, `categories`,
   source enumeration) and `emit`s. The central rule UI builds itself
   from that. First producer: **vision**.
2. **Sink** — no new sending path: delivery goes through the **existing
   `send_reply` method** of the channel plugins (SSoT), wrapped in
   `message_processor.announce_to_channel(channel, recipient, text, media)`
   — recipient resolution (user_mapping → allowlist fallback) included. The
   **scheduler** uses exactly this function too (previously a duplicate in
   `_deliver_announce`, now delegated). `send_reply` can now send `media`
   (photo) along. First sink: **Telegram** (text + photo).
3. **Action** (later) — not every reaction is a "message": webhook,
   script, arming/disarming a camera. Channel send is just *one* kind of action.

**FreeEcho.2 (Puck) as a sink — implemented.** The Puck holds a persistent
WebSocket (`_devices[room]`) and, since the audio bus refactor phase, also accepts
server-initiated push sequences. When the dispatcher calls
`announce_to_channel("freeecho2", recipient, text, media=…)`, it runs
through the same SSoT path as all other sinks (`send_reply` with a dummy
`InboundMessage(sender="system")`):

- **Recipient resolver** (`_resolve_channel_recipient`) resolves empty
  `recipient` values to the first connected device room (FreeEcho.2
  has no allowlist — the hardware is on the LAN, no sender filter).
  Anyone with several Pucks who wants targeted delivery passes
  `recipient="wohnzimmer"` explicitly.
- **`send_reply`** recognizes the autonomous call by `sender == "system"`
  (or `outbound.metadata.proactive=True`) and routes via the
  `AudioOrchestrator` with the chime matching the severity — either
  `play_alarm(with_tts=True, tts_pcm=…)` (conspicuous `alarm_wav` sound) or
  `play_notification(with_tts=True, tts_pcm=…)` (gentle
  `notification_wav` sound). The sequence on the wire:
  `audio_flag(alarm|notification, with_tts=True)` → `audio_flag(tts)` →
  `audio_start` → PCM chunks → `audio_end`. The Puck first plays the
  local sound, buffers the TTS stream in parallel and switches seamlessly to
  the speech — no "speaking out of nowhere" effect.
- **Sound choice via metadata.audio_type** — `_default_deliver` maps
  `ev.severity` to the tuple: `critical` → `"alarm"`, otherwise →
  `"notification"`. The tuple travels via `announce_to_channel(..., metadata=
  {"audio_type": ..., "severity": ..., "category": ...})` and is
  read by the channel. Other sinks (Telegram, email, …) silently ignore the
  field. Schema drift (unknown `audio_type`) falls back to
  `"notification"`, so that a caller bug doesn't swallow the push.
- **User wake reply** stays unchanged (no chime), because `send_reply`
  sees `original.sender == "<room>"` there instead of `"system"`.

Tests: `tests/test_freeecho2_proactive_push.py` — recipient resolver,
send_reply routing (system sender vs. metadata.proactive vs. silent_reply),
audio_type mapping (alarm vs. notification vs. unknown fallback), and
alert_bus severity mapping. Live smoke test with the vision producer → Puck
as sink in the alert rule file (`data/alert_rules.json`).

## Delivery & modes (SSoT, one path for template + LLM)

`_default_deliver(ev, rule)` is the one delivery — both text paths share
it:

1. **Generate text** per `compose` mode (rule field `compose`, otherwise
   `config.ALERT_COMPOSE_DEFAULT`):
   - `"template"` — fixed format string from `AlertEvent` (deterministic,
     no LLM).
   - `"llm"` — `_compose_via_llm` builds a synthetic `InboundMessage` and
     calls **`process_inbound`** (AIfred phrases it; it creates the
     session itself in the process — like the scheduler).
2. **Browser session** (control trail): `record_autonomous_turn` writes the
   turn via the same primitives as `process_inbound` (`routing_table` +
   `create_empty_session` + `update_chat_data` + `write_hub_notification`) →
   appears as a **normal session** in the browser. (In LLM mode,
   `process_inbound` has already done this.)
3. **Channels**: `announce_to_channel` per sink of the rule (SSoT, see above).

A browser session itself counts as a delivery — so alerts are visible
(in the browser) even if a channel is not configured.

## Live clustering (foundation, replaces batch-only)

Live alerts fire at the moment of detection — **before** the describe run
clusters. So that the alert `dedup_key` can deliver "one alert per occurrence"
(instead of a blind time cooldown), clustering is **moved
forward: live in the watcher**.

- The matching core of `vision_cluster` becomes an **incremental
  clusterer** (stateful per source: `(timestamp, pHash) → cluster_id`,
  the same deterministic ID logic).
- The **watcher** computes the pHash from the in-memory frame (cheaper than
  a disk re-read in the batch) and writes the `cluster_id` directly when
  saving the event.
- `vision_cluster` (batch) remains as a **backfill** for events without
  `cluster_id` (legacy data / watcher was off) — same core, SSoT.
- The describe run no longer re-clusters, it only describes representatives.

Side effects: the "freshness gap" in `vision_query_events` (fresh events
without `cluster_id`) disappears, and describe becomes leaner.

Residual risk: a watcher restart resets open clusters → an occurrence
spanning the restart is split (rare, acceptable).

## Throttling / dedup

- `dedup_key` for vision = `cluster_id` (one alert per occurrence; two
  different people shortly after each other = two clusters = two alerts).
- Generic: other producers supply their own keys (scheduler → task ID,
  system → metric name).
- Per rule additionally `min_interval_sec` + optional quiet hours.

## Rule config

Central in `data/alert_rules.json` — a JSON list of rule objects.
**File missing → no rules → no alerts** (safe default; the
feature only activates once you create the file). Unknown keys
are ignored (the schema may grow). Example:

```json
[
  {
    "rule_id": "vision-stranger",
    "producer": "vision",
    "category": "face_unknown",
    "source_id": null,
    "min_severity": "info",
    "sinks": ["telegram"],
    "min_interval_sec": 300,
    "quiet_hours": [22, 7]
  }
]
```

`category`/`source_id` = `null` means "all". `sinks` are channel names from
the `plugin_registry`. `min_interval_sec` is the cooldown per
`(rule, dedup_key)`; `quiet_hours` `[start, end]` (local, wraps past
midnight). Loaded once by `get_default_dispatcher()`.

## Future producers (no pre-building, just plugging in)

System health (GPU temp, disk, service crash), scheduler/reminders,
completion notices of long tasks (deep research, calibration), threshold
watchdogs, calendar/EPIM. Each = "register manifest + `emit`".

## Work packages

1. **Core** — `AlertEvent`, `AlertRule`, `AlertDispatcher` (matching +
   throttle + sink registry). Pure lib, unit-testable without I/O.
2. **Live clustering** — incremental clusterer (SSoT), watcher writes
   `cluster_id` on detection, batch becomes backfill, describe slimmed down.
3. **Telegram proactive send** — sink capability "send to target" (target from
   plugin config), text + photo.
4. **Vision producer** — `emit` from the watcher on `face_unknown` (armed),
   `dedup_key = cluster_id`.
5. **Central rule config** + wiring.
6. **Tests + checks**; later rule UI, more producers/sinks.

## Status & open items

Done: core (package 1), live clustering (2), Telegram proactive send (3),
vision producer + rule config + wiring (4/5). The first vertical
slice is in place: unknown face at an armed camera → Telegram with image
(activates as soon as `data/alert_rules.json` is created).

Open / later:
- Formalize producer registration (manifest plugin) once a
  rule UI arrives — until then the vision producer emits directly.
- More producers (system health, scheduler) and sinks (FreeEcho voice).
- Message: currently template (deterministic); AIfred-phrased optional.
- Live verification needed: watcher restart (live clustering takes effect) and a
  real Telegram send with `data/alert_rules.json` in place.
