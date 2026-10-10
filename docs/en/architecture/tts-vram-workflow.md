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

## Escalation List (FreeEcho.2, Narrator)

Channels with their own speaker (FreeEcho.2) and the narrator do not pick their
engine themselves; they go through the **global escalation list**
(`lib/tts_escalation.py`). Two lists in `settings.json`:

- `tts_hosts`: other machines running TTS containers with the same API
  (`name`, `address`, optional `ports` per engine). This machine is implicit.
- `tts_escalation`: ordered entries `{"engine", "host", "enabled"}`; `host: null`
  = this machine (or cloud). The first fitting entry from the top speaks.

An entry **fits** when it is enabled and:

| Kind | Condition |
|------|-----------|
| Remote (container on `host`) | Its health check answers. The Mini never starts or stops anything there. |
| Local GPU (XTTS, Qwen3, MOSS, Fish) | The container runs — or its measured burn-in peak (`tts_vram_cache`) + `LLAMACPP_TTS_BURNIN_HEADROOM_MB` fits into the **free** memory of the side-channel card (`pick_tts_gpu`); then it is started. Without a measured peak it does not fit. |
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

## Browser — TTS Switching

### Engine Dropdown (Main Settings)
- Switching happens **immediately** (not just changing the setting)
- `set_tts_engine_or_off()` handles it: free VRAM, start the new container, reload the LLM with the profile
- "Off" → `enable_tts=False`, stop the GPU container

### Agent Editor (per Agent)
- Backend dropdown per agent: selects which backend applies to this agent
- "Off" → the agent gets no TTS (`enabled=False`)
- Empty voice → fallback to the agent's engine default from `agents.json`, then the global `tts_voice` (see Voice Resolution)
- Changes are saved only as **settings**, no immediate VRAM switch

### FreeEcho.2 Plugin
- No engine setting of its own — it speaks through the escalation list
- The speech unit (sentence/paragraph/whole) is the one of the speaking engine

## Autoplay + Streaming

| Autoplay | Streaming | Behavior |
|----------|-----------|-----------|
| ON | ON | Realtime: sentences are generated and played during inference |
| ON | OFF | Queue: full audio after inference, then play |
| OFF | * | Audio is generated (play button), but not played automatically. The streaming value is ignored. |

## Voice Resolution

### Browser
SSOT: `_resolve_agent_tts()` in `_tts_streaming_mixin.py`. An agent never borrows
another agent's voice.
1. User's per-agent voice for the active engine (`tts_agent_voices[agent]["voice"]`)
2. The agent's engine default from `data/agents.json` (`tts_voices.<engine>`)
3. `self.tts_voice` (global state default) — only for agents without an engine default

### Escalation List (FreeEcho.2)
SSOT: `resolve_voice()` in `lib/tts_escalation.py`, per engine of the speaking entry.
1. User setting for agent+engine (`tts_agent_voices_per_engine[engine][agent]` in `settings.json`)
2. User setting for AIfred (only if the agent has none)
3. The agent's engine default from `data/agents.json` (`tts_voices.<engine>`, via `get_tts_voice_default()`)
4. AIfred's engine default from `data/agents.json`, if the agent's default has no voice
5. No voice → the entry fails (`TTSFailure`), the list moves on

## Debug Output

On every LLM profile switch, the effective model + context is shown
(`get_effective_model_info()`; FreeEcho.2 and the browser dropdown prefix the
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
| `set_tts_engine_or_off()` | `_tts_config_mixin.py` | Browser dropdown handler |
| `SpeechRun` / `choose_speaker()` | `lib/tts_escalation.py` | Escalation list: selection, failure, announcement |
| `resolve_voice()` | `lib/tts_escalation.py` | Voice/speed/pitch per engine (channels, narrator) |
| `start_speech_stream()` | `lib/speech_synthesis.py` | Sentence-wise PCM stream for channels with a speaker |
| `release_side_channels_for_describer()` | `lib/vision_routing.py` | Describer evicts TTS and STT |
| `_queue_tts_for_agent()` | `_tts_streaming_mixin.py` | Browser TTS generation |
| `_resolve_agent_tts()` | `_tts_streaming_mixin.py` | Browser voice/speed/pitch resolution |
