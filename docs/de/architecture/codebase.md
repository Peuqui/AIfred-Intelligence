# Überblick über die Codebasis

> **English version:** [codebase.md](../../en/architecture/codebase.md)

Wo was liegt. Den Weg einer einzelnen Anfrage beschreibt
[LLM-Call-Architektur](llm-call.md); die Code-Landkarte der Recherche steht in
[Recherche-Pipeline](research-pipeline.md#code-landkarte).

```
AIfred-Intelligence/
├── aifred/
│   ├── aifred.py            # Reflex-App: Seiten, Routen, API-Mount, Start-Verdrahtung
│   ├── backends/            # LLM-Backend-Adapter (llamacpp, vllm, ollama, cloud_api) — siehe backends/README.md
│   ├── lib/                 # Kern-Bibliotheken — hier zuerst suchen
│   │   ├── multi_agent.py         # Agenten, Diskussionsmodi, ein Antwortpfad für alle Agenten
│   │   ├── llm_engine.py · llm_pipeline.py · llm_client.py   # LLM-Calls, Tool-Schleife, Streaming
│   │   ├── prompt_loader.py       # Prompt-Dateien + Schichten-Merge
│   │   ├── message_builder.py     # Message-Formatierung, Perspektive pro Agent
│   │   ├── context_manager.py     # History-Kompression
│   │   ├── intent_detector.py     # Intent, Adressat, Moduswechsel
│   │   ├── research_tools.py · research/ · tools/   # Web-Recherche-Pipeline
│   │   ├── document_store.py · file_manager.py · embeddings.py   # RAG (ChromaDB, bge-m3)
│   │   ├── document_sources.py    # Weitere Dokumentenquellen (Protokoll; Plugins bieten sie an, z. B. Google Drive)
│   │   ├── agent_memory.py        # Langzeitgedächtnis pro Agent
│   │   ├── message_hub.py · message_processor.py · routing_table.py · envelope.py   # Message Hub
│   │   ├── scheduler.py           # Cron-/Intervall-/Einmal-Jobs
│   │   ├── security.py · credential_broker.py · auth.py · oauth/   # Tiers, Secrets, Login, OAuth-Broker
│   │   ├── calibration/           # Kontext-Kalibrierung (llama.cpp-Ablauf, vLLM-Ablauf)
│   │   ├── vision_*.py · frame_sources/ · frame_hub.py   # Vision + Vigilantia
│   │   ├── tts_engines/           # Ein Ordner pro TTS-Engine: <key>/engine.py (TTSEngine-Unterklasse) + i18n.json;
│   │   │                          #   registry.py findet die Ordner — ein neuer Ordner taucht überall auf
│   │   ├── tts_escalation.py      # TTS-Eskalationsliste: welche Engine auf welchem Rechner spricht (SpeechRun, Host-Steuerung per SSH)
│   │   ├── tts_engine_manager.py · tts_vram_cache.py   # Container-Lebenslauf, gemessene VRAM-Spitzen
│   │   ├── speech_synthesis.py · audio_*.py · audio_channels/   # Synthese, STT, Ausgabewege (Browser, FreeEcho.2, lokal)
│   │   ├── api/                   # REST API (FastAPI, unter /api gemountet): chat, agents, audio, announce, tts, documents, vision, …
│   │   ├── i18n/                  # UI-Übersetzungen (de.json, en.json)
│   │   └── config.py              # Defaults und Stellschrauben
│   ├── plugins/
│   │   ├── tools/           # Tool-Plugins (workspace, sandbox, research, vision, google_suite, printer, subagent, …)
│   │   └── channels/        # Channel-Plugins (telegram, discord, email, freeecho2, ai_connect)
│   ├── state/               # Reflex-State, zusammengesetzt aus Feature-Mixins (_chat_mixin.py, …)
│   └── ui/                  # Reflex-UI-Komponenten (modals/, agent_editor/, settings_accordion/, …)
├── prompts/{de,en}/         # Alles, was das LLM sieht — nie im Code hardcodiert
├── data/                    # Laufzeitdaten: settings.json, sessions/, chromadb/, logs/, Caches
├── docker/                  # ChromaDB + SearXNG (docker-compose.yml), tts/<Dienst>/ (lokale TTS-Container) + tts/voices/ (gemeinsame Referenzstimmen)
├── systemd/                 # Unit-Vorlagen, gerendert von scripts/install-services.sh
├── scripts/                 # Installer, llama-swap-Autoscan/-Restart, Patches, Wartung
├── deploy/                  # Optionale Extras (AIfred Bibliothek: Dokumenten-UI hinter nginx)
├── docs/{de,en}/            # Dokumentation (Index: docs/README.md)
└── tests/                   # pytest-Suite
```

**Konventionen**
- Plugins sind atomar: Ein Plugin importiert nie ein anderes Plugin; gemeinsame
  Logik gehört nach `aifred/lib/`. Ein Plugin zu deaktivieren (Verschieben nach
  `plugins/disabled/`) darf nichts kaputt machen. Plugins schreiben:
  [Plugin-Entwicklung](../guides/plugin-development.md).
- Alles, was das LLM liest, liegt in `prompts/de/` und `prompts/en/` — immer in
  beiden Sprachen.
- Zahlen für den User laufen über `format_number()` (`aifred/lib/formatting.py`)
  für locale-abhängige Formatierung.

**Qualitätsprüfungen**
```bash
venv/bin/ruff check aifred/
venv/bin/mypy aifred/ --ignore-missing-imports
venv/bin/python -m pytest
```
