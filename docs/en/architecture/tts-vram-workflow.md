# TTS + VRAM Workflow — FreeEcho.2 & Browser

> **Deutsche Version:** [tts-vram-workflow.md](../../de/architecture/tts-vram-workflow.md)

## Core Principle

**Unload nothing unless room has to be made.**
Everything stays loaded until the next request needs something different.

## GPU TTS Engines

The four GPU engines **XTTS**, **MOSS-TTS**, **Qwen3-TTS** and **Fish-Speech**
occupy VRAM (each a Docker container with GPU).
Piper, Edge, eSpeak and DashScope need no VRAM.

## FreeEcho.2 — Cases

The FreeEcho.2 has its **own TTS engine** (configured in the plugin, independent of the browser).

The FreeEcho.2 pipeline calls `ensure_tts_state(..., check_defer=True)` before
inference. As soon as an LLM is loaded and a GPU TTS other than the running one is
wanted, the switch is **deferred**: inference runs first on the loaded LLM,
`force_tts_switch()` follows before the audio is generated (cases 2 and 4).

Before the deferred switch, `_gpu_tts_combo_fits()` checks whether the active model
has a calibrated TTS variant for the wanted GPU TTS. If not, the voice output is
skipped with an error log (a switch would evict the LLM and still leave no VRAM for
the TTS).

### Case 1: VRAM empty (nothing loaded)
1. Start TTS (e.g. XTTS)
2. Load the LLM with the TTS profile (e.g. `GPT-OSS-120B-A5B-UD-Q8_K_XL-tts-xtts`)
3. Inference
4. Generate audio
5. **Everything stays loaded**

### Case 2: LLM loaded, no TTS (deferred path)
1. Inference with the existing LLM (fast, no reload)
2. Unload everything (free VRAM)
3. Start TTS
4. Restart llama-swap with the TTS profile (the LLM itself loads again on the next request)
5. Generate audio
6. **Everything stays loaded**

Exception: if the LLM already runs on the matching `-tts-<engine>` variant (e.g. the
TTS container stopped itself via its idle watchdog), `_do_switch()` starts the
container next to the warm LLM — no unload, no llama-swap restart.

### Case 3: LLM + correct TTS already loaded
1. Inference with the TTS profile (no reload needed)
2. Generate audio
3. **Everything stays loaded**

### Case 4: LLM + wrong TTS loaded (e.g. MOSS instead of XTTS) — deferred as well
1. Inference with the existing LLM (no reload)
2. Stop the wrong TTS, unload everything
3. Start the correct TTS
4. Load the LLM with the new TTS profile
5. Generate audio
6. **Everything stays loaded**

### Case 5: Only TTS loaded, no LLM
1. Load the LLM with the TTS profile alongside
2. Inference
3. Generate audio
4. **Everything stays loaded**

### Case 6: Only the wrong TTS loaded, no LLM
1. Unload everything
2. Start the correct TTS
3. Load the LLM with the TTS profile
4. Inference
5. Generate audio
6. **Everything stays loaded**

### Case 7: LLM + GPU TTS loaded, plugin set to a lightweight engine (Piper/Edge/eSpeak/DashScope)
1. Stop of the GPU TTS container starts in the background (unless an active pipeline
   still holds it)
2. Inference with the existing LLM
3. `force_tts_switch("")` waits for the stop, restarts llama-swap with the base profile
4. Generate audio with the lightweight engine

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
- Own engine setting in the plugin (credential broker)
- Independent of the browser backend
- Change → setting only, no immediate switch
- The next FreeEcho.2 request uses the new settings

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

### FreeEcho.2
SSOT: `_run_tts()` in `tts_reply.py`. Engine from the plugin setting
(`freeecho2`/`tts_engine`, default `piper`).
1. User setting for agent+engine (`tts_agent_voices_per_engine[engine][agent]` in `settings.json`)
2. User setting for AIfred (only if the agent has none)
3. The agent's engine default from `data/agents.json` (`tts_voices.<engine>`, via `get_tts_voice_default()`)
4. AIfred's engine default from `data/agents.json`, if the agent's default has no voice
5. `PUCK_TTS_FALLBACK_VOICE` (config.py), if the default entry has no `voice` field at all

## Debug Output

On every LLM profile switch, the effective model + context is shown
(`get_effective_model_info()`; FreeEcho.2 and the browser dropdown prefix the
status messages with 🔊):
```
🔊 LLM profile ready: GPT-OSS-120B-A5B-UD-Q8_K_XL-tts-xtts (ctx: 131.072)
```
When the TTS was already running (`force_tts_switch()`): `🔊 LLM profile switched: …`.

On intent detection (`format_intent_result()` in `intent_detector.py`):
```
🎯 Intent: FAKTISCH, Addressee: –, Lang: DE
```

## Code Entry Points

| Function | File | Description |
|----------|-------|-------------|
| `ensure_tts_state()` | `tts_engine_manager.py` | SSOT: checks/establishes the VRAM state |
| `force_tts_switch()` | `tts_engine_manager.py` | After deferred inference: load TTS + switch profile |
| `_do_switch()` | `tts_engine_manager.py` | Full engine switch (unload → load) |
| `set_tts_engine_or_off()` | `_tts_config_mixin.py` | Browser dropdown handler |
| `_run_tts()` | `plugins/channels/freeecho2_channel/tts_reply.py` | FreeEcho.2 audio generation + voice resolution |
| `_ensure_tts_state()` / `_force_tts_switch()` | `plugins/channels/freeecho2_channel/tts_reply.py` | FreeEcho.2 wrappers around the SSOT functions |
| `_queue_tts_for_agent()` | `_tts_streaming_mixin.py` | Browser TTS generation |
| `_resolve_agent_tts()` | `_tts_streaming_mixin.py` | Browser voice/speed/pitch resolution |
