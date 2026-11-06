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
│   │   ├── tts_client.py · tts_service.py · tts_voices.py   # Client of the TTS-Suite service: HTTP/WebSocket, catalog ↔ AIfred's list, voices
│   │   ├── tts_llm_coordination.py · tts_stress_burnin.py   # LLM profile plan, release before a load, TTS burn-in measurement
│   │   ├── gpu_orc_client.py      # Client of GPU-Orc: clear the cards before a load, who holds which card
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
  into `aifred/lib/`. Switching a plugin off (a `.disabled` marker in its folder;
  the code stays where it is) must not break anything. Writing plugins: [Plugin Development](../guides/plugin-development.md).
- Everything the LLM reads lives in `prompts/de/` and `prompts/en/` — both
  languages, always.
- User-facing numbers go through `format_number()` (`aifred/lib/formatting.py`)
  for locale-aware formatting.
- Catch errors where they can occur, and only those (`sqlite3.Error`, `OSError`, `cv2.error`,
  `OLLAMA_ERRORS` …). A broad `except Exception` stands only at a real boundary — a loop that
  must go on, a callback, one rule or camera among several, an action with a visible error
  message — and names its reason and logs the traceback. `tests/test_broad_except_reasons.py`
  rejects `# noqa: BLE001` without a reason.
- In the state, public fields are `rx.Field`, public methods carry `@rx.event`, and what a mixin
  uses from another one is declared under `if TYPE_CHECKING:`. Methods only state code calls are
  private (`_name`): every public method can be triggered by any connected client.
  `tests/test_state_typing_conventions.py` and `tests/test_state_attribute_references.py` keep this.

**Quality checks**
```bash
ruff check aifred tests
mypy aifred/ tests/ --python-executable venv/bin/python --ignore-missing-imports
venv/bin/python -m pytest
```

- ruff and mypy are not in the project venv; install them separately (checked with ruff 0.16 and
  mypy 2.4). mypy gets the project's types and the stubs from `requirements.txt` through
  `--python-executable`. It checks `aifred/` and `tests/` with no exceptions for our own code.
- mypy needs the type patches of `scripts/patch-reflex.py` (see
  [Reflex patches](../guides/deployment.md#reflex-patches)); without them it reports hundreds of
  errors in `aifred/ui`. If it reports "Module not callable", re-check without the cache
  (`--no-incremental`).
- Some tests need files that exist only on a set-up machine: `data/vllm_runtime.yaml` (vLLM
  calibration), `data/epim/test-fixture.epim` and `lib/firebird25/` (EPIM database). Without
  them the EPIM tests skip themselves and the calibration tests fail.
