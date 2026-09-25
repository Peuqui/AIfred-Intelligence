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
4. Generate audio
5. Load the LLM with the TTS profile alongside
6. **Everything stays loaded**

### Case 3: LLM + correct TTS already loaded
1. Inference with the TTS profile (no reload needed)
2. Generate audio
3. **Everything stays loaded**

### Case 4: LLM + wrong TTS loaded (e.g. MOSS instead of XTTS)
1. Unload everything
2. Start the correct TTS
3. Load the LLM with the new TTS profile
4. Inference
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

## Browser — TTS Switching

### Engine Dropdown (Main Settings)
- Switching happens **immediately** (not just changing the setting)
- `set_tts_engine_or_off()` handles it: free VRAM, start the new container, reload the LLM with the profile
- "Off" → `enable_tts=False`, stop the GPU container

### Agent Editor (per Agent)
- Backend dropdown per agent: selects which backend applies to this agent
- "Off" → the agent gets no TTS (`enabled=False`)
- Empty voice → fallback to AIfred's voice of the current backend
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
1. Agent has a voice configured → use it
2. Agent has no voice → AIfred's voice of the current backend
3. AIfred has no voice → `self.tts_voice` (state default)

### FreeEcho.2
1. User setting for agent+engine (from `settings.json`)
2. User setting for AIfred (fallback)
3. `TTS_AGENT_VOICE_DEFAULTS[engine][agent]`
4. `TTS_AGENT_VOICE_DEFAULTS[engine]["aifred"]`
5. `PUCK_TTS_FALLBACK_VOICE` (config.py)

## Debug Output

On every LLM profile switch, the effective model + context is shown:
```
🔊 LLM restarted: GPT-OSS-120B-A5B-UD-Q8_K_XL-tts-xtts (ctx: 131.072)
```

On intent detection:
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
| `_run_tts()` | `freeecho2/__init__.py` | FreeEcho.2 audio generation |
| `_queue_tts_for_agent()` | `_tts_streaming_mixin.py` | Browser TTS generation |
