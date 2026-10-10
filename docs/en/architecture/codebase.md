# Codebase Overview

> **Deutsche Version:** [codebase.md](../../de/architecture/codebase.md)

Where things live. For the path of a single request see
[LLM Call Architecture](llm-call.md); for the research code map see
[Research Pipeline](research-pipeline.md#code-map).

```
AIfred-Intelligence/
├── aifred/
│   ├── aifred.py            # Reflex app: pages, routes, API mount, startup wiring
│   ├── backends/            # LLM backend adapters (llamacpp, vllm, ollama, cloud_api) — see backends/README.md
│   ├── lib/                 # Core libraries — search here first
│   │   ├── multi_agent.py         # Agents, discussion modes, one answer path for all agents
│   │   ├── llm_engine.py · llm_pipeline.py · llm_client.py   # LLM calls, tool loop, streaming
│   │   ├── prompt_loader.py       # Prompt files + layer merge
│   │   ├── message_builder.py     # Message formatting, per-agent perspective
│   │   ├── context_manager.py     # History compression
│   │   ├── intent_detector.py     # Intent, addressee, mode switch
│   │   ├── research_tools.py · research/ · tools/   # Web research pipeline
│   │   ├── document_store.py · file_manager.py · embeddings.py   # RAG (ChromaDB, bge-m3)
│   │   ├── document_sources.py    # Further document sources (protocol; plugins offer them, e.g. Google Drive)
│   │   ├── document_sources.py    # Further document sources (protocol; plugins offer them, e.g. Google Drive)
│   │   ├── agent_memory.py        # Per-agent long-term memory
│   │   ├── message_hub.py · message_processor.py · routing_table.py · envelope.py   # Message Hub
│   │   ├── scheduler.py           # Cron / interval / one-shot jobs
│   │   ├── security.py · credential_broker.py · auth.py · oauth/   # Tiers, secrets, login, OAuth broker
│   │   ├── calibration/           # Context calibration (llama.cpp flow, vLLM flow)
│   │   ├── vision_*.py · frame_sources/ · frame_hub.py   # Vision + Vigilantia
│   │   ├── tts_engines/           # One folder per TTS engine: <key>/engine.py (TTSEngine subclass) + i18n.json;
│   │   │                          #   registry.py discovers the folders — a new folder lights up everywhere
│   │   ├── tts_escalation.py      # TTS escalation list: which engine on which host speaks (SpeechRun, host control over SSH)
│   │   ├── tts_engine_manager.py · tts_vram_cache.py   # Container lifecycle, measured VRAM peaks
│   │   ├── speech_synthesis.py · audio_*.py · audio_channels/   # Synthesis, STT, output paths (browser, FreeEcho.2, local)
│   │   ├── api/                   # REST API (FastAPI, mounted under /api): chat, agents, audio, announce, tts, documents, vision, …
│   │   ├── i18n/                  # UI translations (de.json, en.json)
│   │   └── config.py              # Defaults and tunables
│   ├── plugins/
│   │   ├── tools/           # Tool plugins (workspace, sandbox, research, vision, google_suite, printer, subagent, …)
│   │   └── channels/        # Channel plugins (telegram, discord, email, freeecho2, ai_connect)
│   ├── state/               # Reflex state, composed from feature mixins (_chat_mixin.py, …)
│   └── ui/                  # Reflex UI components (modals/, agent_editor/, settings_accordion/, …)
├── prompts/{de,en}/         # All LLM-visible text — never hardcoded in code
├── data/                    # Runtime data: settings.json, sessions/, chromadb/, logs/, caches
├── docker/                  # ChromaDB + SearXNG (docker-compose.yml), tts/<service>/ (local TTS containers) + tts/voices/ (shared reference voices)
├── systemd/                 # Unit templates, rendered by scripts/install-services.sh
├── scripts/                 # Installers, llama-swap autoscan/restart, patches, maintenance
├── deploy/                  # Optional extras (AIfred Bibliothek: document UI behind nginx)
├── docs/{de,en}/            # Documentation (index: docs/README.md)
└── tests/                   # pytest suite
```

**Conventions**
- Plugins are atomic: a plugin never imports another plugin; shared logic goes
  into `aifred/lib/`. Disabling a plugin (moving it to `plugins/disabled/`) must
  not break anything. Writing plugins: [Plugin Development](../guides/plugin-development.md).
- Everything the LLM reads lives in `prompts/de/` and `prompts/en/` — both
  languages, always.
- User-facing numbers go through `format_number()` (`aifred/lib/formatting.py`)
  for locale-aware formatting.

**Quality checks**
```bash
venv/bin/ruff check aifred/
venv/bin/mypy aifred/ --ignore-missing-imports
venv/bin/python -m pytest
```
