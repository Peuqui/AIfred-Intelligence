# Überblick über die Codebasis

> **English version:** [codebase.md](../../en/architecture/codebase.md)

Wo was liegt. Den Weg einer einzelnen Anfrage beschreibt
[LLM-Call-Architektur](llm-call.md); die Code-Landkarte der Recherche steht in
[Recherche-Pipeline](research-pipeline.md#code-landkarte).

```
AIfred-Intelligence/
├── aifred/
│   ├── server.py            # Der Server (FastAPI/uvicorn): API unter /api, Vue-Oberfläche, /_upload/, /vendor/
│   ├── backends/            # LLM-Backend-Adapter (llamacpp, vllm, ollama, cloud_api) — siehe backends/README.md
│   ├── lib/                 # Kern-Bibliotheken — hier zuerst suchen
│   │   ├── turn_service.py · turn_debate.py · debate_seats.py   # Ein Zug von der Nachricht bis zur Antwort, Diskussionsmodi, Plätze
│   │   ├── llm_pipeline.py · llm_client.py   # LLM-Calls, Tool-Schleife, Streaming
│   │   ├── prompt_loader.py       # Prompt-Dateien + Schichten-Merge
│   │   ├── message_builder.py     # Message-Formatierung, Perspektive pro Agent
│   │   ├── context_manager.py     # History-Kompression
│   │   ├── intent_detector.py     # Intent, Adressat, Moduswechsel
│   │   ├── research_tools.py · research/ · tools/   # Web-Recherche-Pipeline
│   │   ├── document_store.py · file_manager.py · embeddings.py   # RAG (ChromaDB, bge-m3)
│   │   ├── document_sources.py    # Weitere Dokumentenquellen (Protokoll; Plugins bieten sie an, z. B. Google Drive)
│   │   ├── agent_memory.py        # Langzeitgedächtnis pro Agent
│   │   ├── message_hub.py · message_processor.py · routing_table.py · envelope.py   # Message Hub
│   │   ├── scheduler.py           # Cron-/Intervall-/Einmal-Jobs, auch Jobs mit Teilaufträgen
│   │   ├── task_plan.py           # Aufgabenplan (Rahmen, Teile, Positionen, Spalten) und die eine Prüfung
│   │   ├── plan_engine.py · plan_record.py   # Plan-Engine (Teile als Sub-Agenten), Eintragswerkzeug mit Abrufprüfung
│   │   ├── subagent_run.py        # SubagentSession: ein Sub-Agenten-Lauf (delegate_task und Plan-Engine)
│   │   ├── security.py · credential_broker.py · auth.py · oauth/   # Tiers, Secrets, Login, OAuth-Broker
│   │   ├── calibration/           # Kontext-Kalibrierung (llama.cpp-Ablauf, vLLM-Ablauf)
│   │   ├── vision_*.py · frame_sources/ · frame_hub.py   # Vision + Vigilantia
│   │   ├── tts_client.py · tts_service.py · tts_voices.py   # Client der TTS-Suite: HTTP/WebSocket, Katalog ↔ AIfreds Liste, Stimmen
│   │   ├── tts_llm_coordination.py · tts_stress_burnin.py   # Plan des LLM-Profils, Freigabe vor dem Laden, Burn-in-Messung der TTS
│   │   ├── gpu_orc_client.py      # Anschluss an GPU-Orc: Karten vor dem Laden freiräumen, wer welche Karte belegt (leere `GPU_ORC_URL` = aus)
│   │   ├── gpu_orc_protocol.py    # Protokoll von GPU-Orc: Kopie aus dem GPU-Orc-Repo (scripts/sync_clients.py), nie hier ändern
│   │   ├── speech_synthesis.py · audio_*.py · audio_channels/   # Synthese, STT, Ausgabewege (Browser, FreeEcho.2, lokal)
│   │   ├── api/                   # REST API (FastAPI, unter /api gemountet): chat, agents, audio, announce, tts, documents, vision, …
│   │   ├── i18n/                  # UI-Übersetzungen (de.json, en.json)
│   │   └── config.py              # Defaults und Stellschrauben
│   ├── plugins/
│   │   ├── tools/           # Tool-Plugins (workspace, sandbox, research, vision, google_suite, printer, subagent, …)
│   │   └── channels/        # Channel-Plugins (telegram, discord, email, freeecho2, ai_connect)
│   └── theme.py             # Farben (COLORS), Vue lädt sie als CSS-Variablen
├── frontend/                # Oberfläche: Vue 3, Vite, TypeScript (Build nach frontend/dist)
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
  Logik gehört nach `aifred/lib/`. Ein Plugin abzuschalten (Markierung `.disabled`
  in seinem Ordner, der Code bleibt liegen) darf nichts kaputt machen. Plugins schreiben:
  [Plugin-Entwicklung](../guides/plugin-development.md).
- Alles, was das LLM liest, liegt in `prompts/de/` und `prompts/en/` — immer in
  beiden Sprachen.
- Zahlen für den User laufen über `format_number()` (`aifred/lib/formatting.py`)
  für locale-abhängige Formatierung.
- Fehler fängt man dort, wo sie auftreten können, und nur sie (`sqlite3.Error`, `OSError`,
  `cv2.error`, `OLLAMA_ERRORS` …). Ein breites `except Exception` steht nur an einer echten
  Grenze — eine Schleife, die weiterlaufen muss, ein Rückruf, eine Regel oder Kamera unter
  mehreren, eine Aktion mit sichtbarer Fehlermeldung — und nennt seinen Grund und schreibt den
  Traceback ins Log. `tests/test_broad_except_reasons.py` lehnt `# noqa: BLE001` ohne Grund ab.

**Qualitätsprüfungen**
```bash
ruff check aifred tests
mypy aifred/ tests/ --python-executable venv/bin/python --ignore-missing-imports
venv/bin/python -m pytest
```

- ruff und mypy liegen nicht im Projekt-venv; installiere sie getrennt (geprüft mit ruff 0.16 und
  mypy 2.4). Die Typen des Projekts und die Stubs aus `requirements.txt` bekommt mypy über
  `--python-executable`. Geprüft wird `aifred/` und `tests/` ohne Ausnahme für eigenen Code.
- Meldet mypy „Module not callable“, ohne Cache gegenprüfen (`--no-incremental`).
- Einige Tests brauchen Dateien, die nur auf einem eingerichteten Rechner liegen:
  `data/vllm_runtime.yaml` (vLLM-Kalibrierung), `data/epim/test-fixture.epim` und
  `lib/firebird25/` (EPIM-Datenbank). Fehlen sie, überspringen sich die EPIM-Tests, die
  Kalibrier-Tests schlagen fehl.
