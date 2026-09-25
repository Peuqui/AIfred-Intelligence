# Proactive Alert Pipeline

> **Deutsche Version:** [proactive-alerts.md](../../de/architecture/proactive-alerts.md)

Status (2026-09-25): **implemented** (packages 1–5). Vision is the first
producer; Telegram (text + photo) and FreeEcho.2 (chime + voice) are
implemented sinks, and any other channel plugin can be picked as a sink.
Rules are maintained in the Vigilantia settings (rule UI).

AIfred should speak up on its own when something important happens —
not only when asked. Instead of a vision-specific "notifier", we
build a **generic, pluggable pipeline**: vision is just the first
*producer*, Telegram just the first *sink*. If the core is agnostic,
each additional producer/channel costs almost nothing.

## Architecture in one sentence

Any **producers** emit a neutral **`AlertEvent`** to a
central **dispatcher**; a **rule engine** decides (matching,
dedup, quiet hours) which **sinks** (channel plugins) it goes to; the sinks deliver
proactively. The core knows nothing concrete — producers and sinks plug in.

```
Producer (vision, system, scheduler, …)
        │  emit(AlertEvent)
        ▼
   Dispatcher  ──►  Rule engine  ──►  Throttle (dedup per happening + quiet hours)
        │
        ▼
   Sinks (channel plugins: telegram, discord, email, freeecho2, …)
```

## The fixed core (`aifred/lib/alert_bus.py`)

Stays small forever and producer/channel-agnostic.

- **`AlertEvent`** — neutral dataclass:
  - `producer` (e.g. `"vision"`), `category` (`"face_unknown"`),
    `source_id` (`"cam/office"`), `severity` (`"info" | "warning" | "critical"`),
  - `title`, `body` (display text),
  - `dedup_key` (throttle axis, see below),
  - `media` (primary image path; subject view for single-image channels),
    `media_context` (optional second image path: the wide context view of the
    same moment), `media_gallery` (image URLs for the browser session),
  - `session_key` (optional: several sources of one device share one browser
    session), `session_title` (fixed title of the routed session),
  - `timestamp`, `metadata`.
  - No producer-specific fields in the core.
- **`AlertRule`** — `producer`, `sinks` (list of channel names, optionally
  `"channel:target"`), optional filters (`category`, `source_id`,
  `min_severity`), optional `quiet_hours`, `rule_id`, `compose` (text mode,
  see below). Filters are AND-combined, `None` = any.
- **`AlertDispatcher`** — `emit(event)`: find matching rules → skip during
  quiet hours → dedup → hand over to a `deliver` function per rule (injectable
  for tests, default = `_default_deliver`). Throttle state: `(rule, dedup_key) →
  last sent`, recorded only after a successful delivery. The dispatcher core
  (matching + throttle) stays pure and doesn't deliver anything itself — that
  is done by the SSoT delivery (see below).

Gating such as `armed` is **producer-specific** and stays in the
producer (vision only emits when armed) — the core stays agnostic.

## Pluggable roles

1. **Producer** — calls `get_default_dispatcher().emit(...)`. There is no
   formal producer registration (manifest) yet; the vision producer
   (`aifred/lib/vision_alerts.py`) emits directly.
2. **Sink** — no new sending path: delivery goes through the **existing
   `send_reply` method** of the channel plugins (SSoT), wrapped in
   `message_processor.announce_to_channel(channel, recipient, text, *,
   session_id, media, metadata)` — recipient resolution (user_mapping →
   first mapped user → allowlist fallback) included. The **scheduler** uses
   exactly this function too (`_deliver_announce` delegates to it).
   `send_reply` sends `media` (photo) along; Telegram sends an album when
   `metadata.media_context` is set (dual-lens: zoom + wide).
   A sink entry `"channel:target"` is expanded by `resolve_announce_targets`
   into concrete recipients.
3. **Action** (later) — not every reaction is a "message": webhook,
   script, arming/disarming a camera. Channel send is just *one* kind of action.

**FreeEcho.2 (Puck) as a sink — implemented.** The Puck holds a persistent
WebSocket (`_devices[room]`) and, since the audio bus refactor phase, also accepts
server-initiated push sequences. When the dispatcher calls
`announce_to_channel("freeecho2", recipient, text, …)`, it runs
through the same SSoT path as all other sinks (`send_reply` with a dummy
`InboundMessage(sender="system")`):

- **Target expansion** (`resolve_announce_targets`): sink `"freeecho2"` or
  `"freeecho2:*"` → all currently connected Pucks, `"freeecho2:@group"` → the
  connected rooms of that group from `data/freeecho2_groups.json`,
  `"freeecho2:<room>"` → that room, but only if it is connected (otherwise no
  recipient, no send). FreeEcho.2 has no allowlist — the hardware is on the
  LAN, no sender filter. (`_resolve_channel_recipient` resolves an empty
  recipient to the first connected room — the path for direct calls without
  a target expansion, such as the scheduler.)
- **`send_reply`** recognizes the autonomous call by `sender == "system"`
  (or `outbound.metadata.proactive=True`) and puts chime + TTS into the
  room's **alert queue** (`enqueue_alert`, `alert_queue.py`): one worker per
  room plays the items one after another via the `AudioOrchestrator` — either
  `play_alarm(with_tts=True, tts_pcm=…)` (conspicuous `alarm_wav` sound) or
  `play_notification(with_tts=True, tts_pcm=…)` (gentle
  `notification_wav` sound) — and waits for the Puck's `_done` before the next
  item (with a timeout derived from the playback length). The emit path does
  not block. The sequence on the wire:
  `audio_flag(alarm|notification, with_tts=True)` → `audio_flag(tts)` →
  `audio_start` → PCM chunks → `audio_end`, then `done`. The Puck first plays the
  local sound, buffers the TTS stream in parallel and switches seamlessly to
  the speech — no "speaking out of nowhere" effect.
- **Sound choice via metadata.audio_type** — `_default_deliver` maps
  `ev.severity`: `critical` and `warning` → `"alarm"`, otherwise (`info`) →
  `"notification"`. The value travels via `announce_to_channel(..., metadata=
  {"audio_type": ..., "severity": ..., "category": ...})` and is
  read by the channel. Other sinks (Telegram, email, …) silently ignore the
  field. Schema drift (unknown `audio_type`) falls back to
  `"notification"`, so that a caller bug doesn't swallow the push.
- **User wake reply** stays unchanged (no chime), because `send_reply`
  sees `original.sender == "<room>"` there instead of `"system"`.

Tests: `tests/test_freeecho2_proactive_push.py` — recipient resolver,
send_reply routing (system sender vs. metadata.proactive vs. silent_reply),
audio_type mapping (alarm vs. notification vs. unknown fallback), and
alert_bus severity mapping (critical/warning → alarm, info → notification);
`tests/test_freeecho2_alert_queue.py` — the per-room alert queue.

## Delivery & modes (SSoT, one path for all text modes)

`_default_deliver(ev, rule)` is the one delivery — all text modes share
it:

1. **Generate text** per `compose` mode (rule field `compose`, otherwise
   `config.ALERT_COMPOSE_DEFAULT`, currently `"template"`):
   - `"template"` — fixed format string from `AlertEvent` (deterministic,
     no LLM).
   - `"llm"` — `_compose_via_llm` builds a synthetic `InboundMessage` and
     calls **`process_inbound`** (AIfred phrases it; it creates the
     session itself in the process — like the scheduler). Sees only title
     and body, not the image.
   - `"vlm"` — `_describe_media_via_vlm` has the active VLM describe the
     happening (new images of a running burst as a chapter, otherwise the
     cluster's image series, otherwise the single moment zoom + wide); the
     description goes into the body.
   - `"vlm+llm"` — VLM description, then AIfred phrases the final text with
     it as context.

   If the LLM or VLM fails, the template text stays. A VLM description
   is written back to the vision events (`apply_description_to_events` /
   `apply_cluster_description`), so the nightly bulk describe skips them.
2. **Browser session** (control trail): `record_autonomous_turn` writes the
   turn via the same primitives as `process_inbound` (`routing_table` +
   `create_empty_session` + `update_chat_data` + `write_hub_notification`) →
   appears as a **normal session** in the browser, with `media_gallery` as
   the images. (In LLM mode, `process_inbound` has already done this.)
   `session_title` is set as long as the session has no title.
3. **Channels**: `announce_to_channel` per sink (and per expanded recipient)
   of the rule (SSoT, see above), with the alert's `session_id`, so a reply
   to an alert mail lands in that session.

A browser session itself counts as a delivery — so alerts are visible
(in the browser) even if a channel is not configured.

## Live clustering (foundation, replaces batch-only)

Live alerts fire at the moment of detection — **before** the describe run
clusters. So that the alert `dedup_key` can deliver "one alert per occurrence"
(instead of a blind time cooldown), clustering happens **live in the
watcher**.

- The matching core is the **`IncrementalClusterer`** in `vision_cluster`
  (stateful per source, one open cluster): an event joins it as long as the
  gap to the last event is ≤ `VISION_CLUSTER_GAP_SECONDS` and the total
  duration ≤ `VISION_CLUSTER_MAX_SECONDS`, otherwise a new happening begins.
  The pHash only serves as the ID suffix and validity check (0 = unusable),
  not for splitting; the ID `{source-slug}-{start-ts}-{hash-prefix}` is
  deterministic.
- The **watcher** computes the pHash from the in-memory frame
  (`_cluster_id_for`, cheaper than a disk re-read in the batch) and writes the
  `cluster_id` directly when saving the event.
- `cluster_events` (batch) remains as a **backfill** for events without
  `cluster_id` (legacy data / watcher was off) — same core, SSoT.
- The bulk describe run only clusters events without `cluster_id` and
  describes each happening once as a keyframe sequence.

Side effects: the "freshness gap" in `vision_query_events` (fresh events
without `cluster_id`) disappears, and describe becomes leaner.

Residual risk: a watcher restart resets open clusters → an occurrence
spanning the restart is split (rare, acceptable).

## Throttling / dedup

- A non-empty `dedup_key` identifies one happening: every repeat of the
  same key is suppressed for as long as the dispatcher remembers it
  (`ALERT_DEDUP_RETENTION_SEC`, 1800 s); a new key alerts immediately.
  Events without a key are never throttled. There is no time cooldown per
  rule (anymore).
- Vision: `dedup_key` = `cluster_id` (one alert per occurrence; two
  different people shortly after each other = two clusters = two alerts),
  fallback `source_id:category`. The burst report (`BurstReport`, on sources
  with a face-hunt burst) appends a
  running index (`…:burst-report-<n>`): there, several messages per happening
  are intended — a summary with the best image and all names, then follow-ups
  only on real news (new name, band upgrade, more people).
- Other producers would supply their own keys (scheduler → task ID,
  system → metric name).
- Per rule optional `quiet_hours`. In addition, the vision producer
  filters per camera (source settings): `alerts_enabled`, `alert_types`,
  `quiet_enabled` + `quiet_start`/`quiet_end`.

## Rule config

Central in `data/alert_rules.json` — a JSON list of rule objects.
**File missing → no rules → no alerts** (safe default; the
feature only activates once rules exist). Unknown keys
are ignored (the schema may grow). Example:

```json
[
  {
    "rule_id": "vision-face_unknown",
    "producer": "vision",
    "category": "face_unknown",
    "source_id": null,
    "min_severity": "info",
    "sinks": ["telegram", "freeecho2"],
    "compose": "vlm",
    "quiet_hours": [22, 7]
  }
]
```

`category`/`source_id` = `null` means "all". `sinks` are channel names from
the `plugin_registry`, optionally with a target (`"freeecho2:@downstairs"`).
`quiet_hours` `[start, end]` (local, wraps past midnight). Loaded by
`get_default_dispatcher()`; `reload_rules()` rebuilds the dispatcher live
(the throttle state is reset in the process).

**Rule UI:** the Vigilantia settings (`_alert_rules_section` in
`aifred/ui/vision_settings.py`) show one row per vision category (`person`,
`vehicle`, `animal`, `face_known`, `face_unsure`, `face_unknown`), one switch
per available channel (discovered via `plugin_registry.all_channels()`) and
an "image text" switch (`compose: "vlm"`). Changes write
`data/alert_rules.json` (`rule_id` = `vision-<category>`) and call
`reload_rules()` — effective without a service restart.

## Future producers (no pre-building, just plugging in)

System health (GPU temp, disk, service crash), scheduler/reminders,
completion notices of long tasks (deep research, calibration), threshold
watchdogs, calendar/EPIM. Each = "`emit` an `AlertEvent`".

## Work packages

1. **Core** — `AlertEvent`, `AlertRule`, `AlertDispatcher` (matching +
   throttle). Pure lib, unit-testable without I/O. — done
2. **Live clustering** — incremental clusterer (SSoT), watcher writes
   `cluster_id` on detection, batch becomes backfill, describe slimmed down. — done
3. **Telegram proactive send** — sink capability "send to target", text + photo. — done
4. **Vision producer** — `emit` from the watcher (armed), `dedup_key = cluster_id`. — done
5. **Central rule config** + wiring. — done
6. **Tests + checks** (`tests/test_alert_bus.py`, `tests/test_vision_alerts.py`,
   FreeEcho.2 tests see above); rule UI — done; more producers/sinks — open.

## Status & open items

Done: core (package 1), live clustering (2), Telegram proactive send (3),
vision producer + rule config + wiring (4/5), rule UI, FreeEcho.2 sink,
compose modes `template`/`llm`/`vlm`/`vlm+llm`. The vision producer reports
faces (`face_known`, `face_unsure`, `face_unknown`), persons (`person`) and
edge-AI objects (`vehicle`, `animal`) of armed cameras; on sources with a
face-hunt burst, face and person detections go out as a burst report,
elsewhere as immediate alerts.

Open / later:
- Formalize producer registration (manifest) — the rule UI currently
  knows only the fixed vision categories; the vision producer emits directly.
- More producers (system health, scheduler as an alert producer).
- Actions (webhook, script, arming/disarming a camera).
