# TTS + VRAM Workflow — FreeEcho.2 & Browser

> **Deutsche Version:** [tts-vram-workflow.md](../../de/architecture/tts-vram-workflow.md)

## Core Principle

**Unload nothing unless room has to be made.**
Everything stays loaded until the next request needs something different.

## GPU TTS Engines

The four GPU engines **XTTS**, **MOSS-TTS**, **Qwen3-TTS** and **Fish-Speech**
occupy VRAM (each a Docker container with GPU, one port per engine: `default_port`).
Piper, Edge, eSpeak and DashScope need no VRAM. The same containers can run on
another machine (entry in `tts_hosts`).

The engine the LLM profile (`<model>-tts-<engine>`) reserves room for is switched by
the browser (`ensure_tts_state`). Channels never switch it.

## Escalation List (Browser, FreeEcho.2, Narrator)

The browser, channels with their own speaker (FreeEcho.2) and the narrator do not
pick their engine themselves; they go through the **global escalation list**
(`lib/tts_escalation.py`). Two lists in `settings.json`:

- `tts_hosts`: other machines running TTS containers with the same API
  (`name`, `address`, optional `ports` per engine). This machine is implicit.
- `tts_escalation`: ordered entries `{"engine", "host", "enabled"}`; `host: null`
  = this machine (or cloud). The first fitting entry from the top speaks.

An entry **fits** when it is enabled and:

| Kind | Condition |
|------|-----------|
| Remote (container on `host`) | Its health check answers. The Mini never starts or stops anything there. |
| Local GPU (XTTS, Qwen3, MOSS, Fish) | The container runs — or its measured burn-in peak (`tts_vram_cache`) + `LLAMACPP_TTS_BURNIN_HEADROOM_MB` fits into the **free** memory of a card: preferably the side-channel card (`pick_tts_gpu`), else the card with the most free memory; it is started there. Without a measured peak it does not fit. When a model that needs the card loads later, the backends' GPU guard stops the container (`_release_tts_for_load`), except the engine a `-tts-<engine>` profile reserves room for on the side-channel card. |
| Local without GPU / cloud | The engine is available (`is_running`; DashScope: API key set). |

**The main model is never reloaded for TTS.** If the local TTS does not fit (e.g.
DeepSeek on all cards), the list silently moves on — Aragon, cloud, Piper.

**Failure:** every engine raises `TTSFailure` with a reason (`unreachable`, `engine`,
`software`) instead of returning `None`. `SpeechRun` (one reply/announcement):

- Failure **before the first sound** → silently to the next entry (debug line only).
- Failure **mid-text** → the successor first announces the voice change and its
  reason (`tts_switch_*` in `lib/i18n/{de,en}.json`), then speaks the unfinished sentence.
- A failed entry stays out for the rest of the reply.

The narrator takes the topmost fitting entry (or the engine the agent names) and keeps
it for the whole file — no voice change in the middle of an audiobook.

**Priority on the cards:** main model > describer > TTS > STT. The describer counts the
VRAM of the TTS container and the Whisper GPU worker as free and releases both before
loading (`release_side_channels_for_describer`); a running announcement then moves on
to the next entry.

Debug console and `debug.log`:
```
🔊 [FreeEcho.2 buero] TTS escalation: skip xtts@Aragon (not serving on 10.0.0.2)
🔊 [FreeEcho.2 buero] TTS escalation: qwen3local@local speaks
🔊 [FreeEcho.2 buero] TTS escalation: qwen3local@local failed (unreachable): …
🔊 [FreeEcho.2 buero] TTS escalation: voice change announced, dashscope@local continues
```

## Another Machine as TTS Host

Any machine with an NVIDIA GPU, Docker and the NVIDIA Container Toolkit (Linux or WSL2) can
serve XTTS and Qwen3-TTS for AIfred — same containers, same API. AIfred starts and stops them
there via SSH, just like locally: the containers stop themselves after idle (`*_KEEP_ALIVE`),
AIfred starts them on demand and waits until the model is loaded.

1. Clone or update the repo (`git pull --ff-only`), build the images:
   `docker compose build` in `docker/tts/xtts` and `docker/tts/qwen3-tts`.
2. Next to each `docker-compose.yml` a `.env` (not in Git):
   ```
   TTS_GPU_UUID=GPU-…            # nvidia-smi -L
   XTTS_KEEP_ALIVE=30            # or QWEN3_KEEP_ALIVE=30: idle minutes until self-stop
   ```
3. AIfred's control key (AIfred machine: `~/.ssh/aifred_tts_host.pub`) in the TTS host's
   `~/.ssh/authorized_keys`, pinned to the control script — AIfred can only start and stop TTS
   containers with it, nothing else:
   ```
   command="<repo>/scripts/tts-host-ctl.sh",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA… aifred-tts-host@…
   ```
4. Make ports 5051/5052 reachable from the AIfred machine (firewall; under WSL2 in NAT mode
   `netsh interface portproxy`), and the SSH port as well.
5. In AIfred: spoken output → "Other machines" add name, address and SSH target
   (`user@host:port`), then add the entries "XTTS · <name>" / "Qwen3-TTS · <name>" and order
   them. The machine's switch on the main page stops its containers (VRAM free) or allows AIfred
   to start them. Different ports: `tts_hosts[].ports` in `settings.json`.

Voices come from the repo's `docker/tts/voices/` — same state on both machines, otherwise the
voice is missing and the entry fails.

## Browser — Menu

### Audio Section (Main Settings)
- Switches **spoken output on/off** (`set_enable_tts`) and **Auto-Play** (global, default off, `tts_autoplay`)
- **List:** arrows (order), on/off, label "engine · local/host/cloud/CPU", the engine's speech unit
  (`tts_toggles_per_engine[engine].unit`), delete; "Add" offers only engines whose image is built.
  **Hosts:** name and address (ports only in `settings.json`)
- "VRAM reserved" marks `planned_tts_engine()`. When reordering or switching changes that engine,
  `_apply_planned_tts()` re-plans the VRAM (`ensure_tts_state`, possibly an LLM profile switch) —
  only here, never while speaking
- Before every reply `_phase_tts_container_checks()` only starts the reserved engine if it is not
  running; another local engine the list started into free VRAM is left alone

### Agent Editor (per Agent)
- The engine dropdown only edits the voice per engine (`tts_agent_voices_per_engine`) — who speaks
  is the list's decision
- "Off" mutes the agent for every engine (`tts_agents[agent].enabled`); the per-agent language
  lives there too (`tts_agents[agent].language`)

### FreeEcho.2 Plugin
- No engine setting of its own — it speaks through the escalation list
- The speech unit (sentence/paragraph/whole) is the one of the speaking engine

### Chat Note
Next to the bubble's play button: who spoke (`SpeechRun.note()`, field `tts_note`), with the reason
of a switch, e.g. "🔊 XTTS · Aragon → DashScope · cloud (server outage)". While streaming it
arrives via the `bubble_tts_note` push (custom.js).

## Autoplay + Streaming

| Autoplay | Streaming | Behavior |
|----------|-----------|-----------|
| ON | ON | Realtime: sentences are generated and played during inference |
| ON | OFF | Queue: full audio after inference, then play |
| OFF | * | Audio is generated (play button), but not played automatically. The streaming value is ignored. |

## Voice Resolution

SSOT for every path (browser, FreeEcho.2, re-synthesis): `resolve_voice()` in
`lib/tts_escalation.py`, per engine of the speaking entry. An agent never borrows another
agent's voice.
1. User setting for agent+engine (`tts_agent_voices_per_engine[engine][agent]` in `settings.json`)
2. User setting for AIfred (only if the agent has none)
3. The agent's engine default from `data/agents.json` (`tts_voices.<engine>`, via `get_tts_voice_default()`)
4. AIfred's engine default from `data/agents.json`, if the agent's default has no voice
5. No voice → the entry fails (`TTSFailure`), the list moves on

The narrator uses `narrator_voices[engine]`, else the engine's first own voice.

## Debug Output

On every LLM profile switch, the effective model + context is shown
(`get_effective_model_info()`; the browser menu prefixes the
status messages with 🔊):
```
🔊 LLM profile ready: GPT-OSS-120B-A5B-UD-Q8_K_XL-tts-xtts (ctx: 131.072)
```

On intent detection (`format_intent_result()` in `intent_detector.py`):
```
🎯 Intent: FAKTISCH, Addressee: –, Lang: DE
```

## Code Entry Points

| Function | File | Description |
|----------|-------|-------------|
| `ensure_tts_state()` | `tts_engine_manager.py` | SSOT: checks/establishes the VRAM state |
| `_do_switch()` | `tts_engine_manager.py` | Full engine switch (unload → load) |
| `_edit_tts_lists()` / `_apply_planned_tts()` | `_tts_config_mixin.py` | List editor in the menu, VRAM re-planning |
| `SpeechRun` / `choose_speaker()` | `lib/tts_escalation.py` | Escalation list: selection, failure, announcement |
| `resolve_voice()` | `lib/tts_escalation.py` | Voice/speed/pitch per engine (channels, narrator) |
| `start_speech_stream()` | `lib/speech_synthesis.py` | Sentence-wise PCM stream for channels with a speaker |
| `release_side_channels_for_describer()` | `lib/vision_routing.py` | Describer evicts TTS and STT |
| `_queue_tts_for_agent()` / `_tts_generate_sentence_async()` | `_tts_streaming_mixin.py` | Browser: queue and sentence streaming via `SpeechRun` |
