# Konfiguration & Betrieb

> **English version:** [configuration.md](../../en/guides/configuration.md)

Backends, Einstellungen, Reasoning, History-Kompression, der Vector Store, die wichtigsten
Stellschrauben in `config.py`, Performance-Tuning und was Mehrbenutzerbetrieb bedeutet.
Installation und Dienste: [Einrichtungsanleitung](deployment.md).

---

## Backends

Umschaltbar in den UI-Einstellungen:

| Backend | Modelle | Hinweise |
|---|---|---|
| **llama.cpp** über llama-swap | GGUF | Beste Rohleistung und volle GPU-Kontrolle; dreistufiger Aufbau **llama-swap** (Go-Proxy, Modellverwaltung) → **llama-server** → **llama.cpp**. Autoscan + Kalibrierung, siehe [llama.cpp-Setup](llamacpp-setup.md) |
| **vLLM** | safetensors-Checkpoints (z. B. NVFP4, MXFP4, FP8) | Läuft als `-vllm`-Einträge **unter llama-swap**, gleiche URL und gleicher Katalog wie llama.cpp; eigene Kalibrierung, siehe [vLLM-Kalibrierung](../architecture/calibration-vllm.md) |
| **Ollama** | GGUF | Einfachstes Setup, automatische Modellverwaltung |
| **Cloud-APIs** | Provider-Modelle | Claude (Anthropic), Qwen (DashScope), DeepSeek, Kimi (Moonshot) — OpenAI-kompatibel |

**Cloud-APIs** brauchen nur den Key in `.env` (`ANTHROPIC_API_KEY`,
`DASHSCOPE_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`). Die Modelllisten werden
live vom `/models`-Endpoint des Providers geholt; jeder Provider merkt sich sein
zuletzt genutztes Modell; `-vl`-Varianten bleiben aus dem Dropdown für das Haupt-LLM draußen; der Token-Verbrauch
erscheint in der Debug-Konsole. Praktisch, um große Modelle ohne Hardware auszuprobieren,
für Laptops ohne GPU oder um Cloud- und lokale Qualität zu vergleichen. Die History-Kompression
nutzt immer lokale Modelle (bei denen das echte Kontextlimit bekannt ist).

**GPU-Kompatibilität:** Beim Start liest AIfred die Compute Capability jeder GPU
und warnt, wenn ein Backend nur einen Teil der Karten oder gar keine nutzen kann
(`aifred/lib/gpu_detection.py`).

---

## Einstellungen

Globale Einstellungen liegen in `data/settings.json` und werden von der UI, der REST API
(`PATCH /api/settings`) und dem Message Hub gleichermaßen geschrieben.

- **`backend_models`** ist die Single Source of Truth für jede Modellrolle, pro
  Backend — beim Wechsel des Backends werden dessen Modelle wiederhergestellt:

  ```json
  {
    "backend_type": "llamacpp",
    "backend_models": {
      "llamacpp": {
        "aifred": "<model id>",
        "automatik": "<model id>",
        "vision": "<model id>",
        "sokrates": "",
        "salomo": ""
      },
      "ollama": { "aifred": "qwen3:4b-instruct-2507-q4_K_M", "vision": "qwen3-vl:4b" }
    },
    "agent_tuning": {
      "aifred": { "temperature": 0.7, "reasoning": true, "thinking": true, "reasoning_effort": "" }
    }
  }
  ```

  Eine leere Rolle fällt auf AIfreds Modell zurück. Jeder Agent (auch eigene) hat
  seinen eigenen Eintrag.
- **`agent_tuning`** enthält pro Agent Sampling, Temperatur, Reasoning, Thinking,
  Reasoning Effort, Persönlichkeit und die Schalter für Speed-Varianten.
- Beim ersten Start werden die Standardwerte aus `aifred/lib/config.py`
  (`BACKEND_DEFAULT_MODELS`) verwendet.
- Einstellungen pro Session (Agent, Diskussionsmodus, Recherchemodus) liegen **nicht** hier,
  sondern in der Session-Datei — siehe [REST API → Global vs. pro Session](rest-api.md#global-vs-pro-session).

**Persistenz der Sampling-Parameter**

| Parameter | Bei Neustart | Bei Modellwechsel |
|---|---|---|
| Temperatur | bleibt erhalten | auf YAML zurückgesetzt |
| Top-K, Top-P, Min-P, Repeat Penalty | auf YAML zurückgesetzt | auf YAML zurückgesetzt |

Maßgeblich für die Sampling-Standardwerte sind die Flags `--temp`, `--top-k`,
`--top-p`, `--min-p` und `--repeat-penalty` im Eintrag des Modells in
`~/.config/llama-swap/config.yaml`.

---

## Reasoning und Thinking

Zwei voneinander unabhängige Schalter pro Agent in den LLM-Einstellungen:

| Schalter | Wirkung |
|---|---|
| 💭 **Reasoning** | Fügt die Chain-of-Thought-Anweisungen ein (Prompt-Schicht 2) — funktioniert mit jedem Modell, Instruct-Modelle profitieren am meisten |
| 🧠 **Thinking** | Schickt `enable_thinking` an Thinking-fähige Modelle (Qwen3, QwQ, NemoTron, …), damit sie `<think>`-Blöcke erzeugen |

- **Reasoning Effort:** Für Modelle, deren Chat-Template Stufen kennt
  (z. B. `low` … `xhigh`), wählt ein Selektor pro Agent eine aus; leer bedeutet den
  Standard des Templates, der im Label angezeigt wird.
- Die Reasoning-Ausgabe erscheint als Collapsible mit Modellname und Inferenzzeit.
- Die Temperatur ist unabhängig vom Reasoning (Intent-Erkennung oder manueller Wert).
- Das Automatik-LLM läuft immer mit ausgeschaltetem Thinking — seine Entscheidungen sind schnell und
  strukturiert.
- Tool-Ketten mit Vision: Schalte **Thinking für das Vision-LLM aus** — Reasoning-Modelle
  mit Speculative Decoding (MoE/MTP) können nach dem Thinking Tool-Calls
  verschlucken.

---

## History-Kompression

**Vor** jedem LLM-Aufruf läuft eine Prüfung. Bei 70 % Kontextauslastung werden die ältesten
Nachrichten zu einer Zusammenfassung komprimiert:

| Parameter (`config.py`) | Wert | Bedeutung |
|---|---|---|
| `HISTORY_COMPRESSION_TRIGGER` | 0.7 | bei 70 % Auslastung komprimieren |
| `HISTORY_COMPRESSION_TARGET` | 0.3 | Ziel nach der Kompression (Platz für ~2 Roundtrips) |
| `HISTORY_SUMMARY_RATIO` | 0.25 | Zusammenfassung = 25 % des komprimierten Inhalts (4:1) |
| `HISTORY_SUMMARY_MIN_TOKENS` | 500 | Minimum für eine sinnvolle Zusammenfassung |
| `HISTORY_SUMMARY_TOLERANCE` | 0.5 | erlaubte Überschreitung, darüber wird gekürzt |
| `HISTORY_SUMMARY_MAX_RATIO` | 0.2 | höchstens 20 % des Kontexts für Zusammenfassungen |
| `HISTORY_MAX_SUMMARIES` | 10 | harte Obergrenze für behaltene Zusammenfassungen |

**Algorithmus**
1. Vorab-Prüfung vor jedem LLM-Aufruf, Trigger bei 70 %
2. Maximale Anzahl an Zusammenfassungen aus der Kontextgröße (20 % Budget / 500 Tokens, gedeckelt bei 10)
3. Zu viele Zusammenfassungen → die älteste fliegt zuerst raus (FIFO)
4. Die ältesten Nachrichten sammeln, bis weniger als 30 % übrig sind
5. Diese 4:1 zu einer Zusammenfassung komprimieren
6. Neue History = [Zusammenfassungen] + [verbleibende Nachrichten]

| Kontext | Trigger | Ziel | Komprimiert | Zusammenfassung |
|---|---|---|---|---|
| 7K | 4.900 tok | 2.100 tok | ~2.800 tok | ~700 tok |
| 40K | 28.000 tok | 12.000 tok | ~16.000 tok | ~4.000 tok |
| 200K | 140.000 tok | 60.000 tok | ~80.000 tok | ~20.000 tok |

Die Token-Schätzung ignoriert `<details>`-, `<span>`- und `<think>`-Blöcke (sie werden
nicht ans LLM geschickt). Zusammenfassungen erscheinen im Chat inline an der Stelle, an der die Kompression
stattfand, als Collapsibles; das FIFO-Limit gilt nur für `llm_history` — die
sichtbare `chat_history` behält jede Zusammenfassung.

---

## ChromaDB: Dokumente & Agent-Memory

ChromaDB läuft in Docker (Daten in `data/chromadb/`) und enthält zwei Arten von
Collections, beide mit **bge-m3** eingebettet (mehrsprachig, 8192 Token Kontext,
1024 Dimensionen; `aifred/lib/embeddings.py`):

| Collection | Inhalt | Zugriff |
|---|---|---|
| `aifred_documents` | Indexierte Dokumente (token-genaue Chunks) | `search_documents`-Tool; Dokument-UI und Workspace-Plugin |
| `agent_memory_<agent_id>` | Langzeitgedächtnis pro Agent | Wird vor einer Antwort abgerufen (neueste Einträge + semantische Suche); Memory-Tools für den Agenten |

Das Embedding-Modell wird vom Embed-Profil in llama-swap bereitgestellt oder, als zweite
Wahl, von Ollama (`ollama pull bge-m3`). Massen-Indexierung läuft im GPU-Modus, einzelne
Abfragen im CPU-Modus (keine VRAM-Konkurrenz mit dem aktiven LLM).

Ergebnisse der Web-Recherche werden **nicht** gespeichert — jede Recherche läuft frisch.

**Wartung:** Einträge im Einstellungs-Modal (Tabs Datenbank und
Memory) durchsuchen und löschen. `scripts/chromadb-vacuum.sh` gibt den durch Löschungen freigewordenen Platz an das
Betriebssystem zurück (stoppt den Container für einige Sekunden).

**Zurücksetzen** (löscht indexierte Dokumente und Erinnerungen):

```bash
cd docker
docker compose stop chromadb
sudo rm -rf ../data/chromadb/   # created by the container as root
docker compose up -d chromadb
```

Eine einzelne Collection lässt sich im Einstellungs-Modal oder über den Python-Client
löschen (`chromadb.HttpClient(host='localhost', port=8000).delete_collection(...)`).

---

## Weitere Stellschrauben in `config.py`

```python
# Intent-based temperature (auto mode)
INTENT_TEMPERATURE_FAKTISCH = 0.2    # factual
INTENT_TEMPERATURE_GEMISCHT = 0.5    # mixed
INTENT_TEMPERATURE_KREATIV = 1.0     # creative
SOKRATES_TEMPERATURE_OFFSET = 0.2    # added to AIfred's temperature
SALOMO_TEMPERATURE_OFFSET = 0.3

# Security
SECURITY_MAX_TOOL_CHAIN_DEPTH = 100  # tool calls per request

# Default models per backend
BACKEND_DEFAULT_MODELS = {...}       # Ollama: qwen3:4b-instruct-2507-q4_K_M, qwen3-vl:4b
```

URLs: `LLAMACPP_URL` (Standard `http://localhost:11435/v1`, llama-swap — wird auch
von vLLM genutzt) und der Ollama-Standard `http://localhost:11434`.

Der Ollama-Client hat kein HTTP-Timeout (`httpx.Timeout(None)`), damit lange erste
Tokens bei großen Recherche-Kontexten nicht abbrechen.

**Neustart-Button** in der UI: führt `systemctl restart aifred-intelligence` aus
(braucht die [Polkit-Regel](deployment.md#polkit-regel-neustart-ohne-sudo)); der
Browser lädt nach kurzer Verzögerung neu, Sessions bleiben erhalten.

---

## Performance

### Ollama: `OLLAMA_NUM_PARALLEL=1`

Ollamas Standard von 2 parallelen Slots **verdoppelt die KV-Cache-Allokation** für einen
Slot, den ein einzelner User nie nutzt. Mit 1 passten bei einem 30B-Modell ~222K Kontext statt
~111K auf dieselben GPUs.

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d/
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_NUM_PARALLEL=1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Behalte 2+ nur bei echter gleichzeitiger Last durch mehrere User. Danach neu kalibrieren.

### llama.cpp vs. Ollama

Historische Messung (Qwen3-30B-A3B Q8_0, 2× Tesla P40); der relative
Vorsprung überträgt sich auf neuere Hardware:

| Metrik | llama.cpp | Ollama | Vorteil |
|---|:---:|:---:|:---:|
| Zeit bis zum ersten Token | 1,1 s | 1,5 s | llama.cpp −27 % |
| Generierung | 39,3 tok/s | 27,4 tok/s | llama.cpp +43 % |
| Prompt Processing | 1.116 tok/s | 862 tok/s | llama.cpp +30 % |
| Intent-Erkennung | 0,8 s | 0,7 s | ähnlich |

Wähle **llama.cpp** für Geschwindigkeit, Multi-GPU-Kontrolle über Tensor-Split und große
Kontexte; **Ollama** für den schnellsten Einstieg. Empfohlene llama-server-Parameter
pro Modell: [Modellparameter](../benchmarks/model-params.md).

---

## Mehrere Benutzer

AIfred ist ein **persönlicher Assistent** — gebaut für einen User, gut geeignet für einen Haushalt
mit zwei oder drei Personen.

**Pro Benutzer:**
- Konten mit Passwort (bcrypt) und einer Registrierungs-Whitelist, verwaltet mit
  `aifred-admin` (siehe [Einrichtungsanleitung](deployment.md#benutzerverwaltung))
- Chat-Sessions gehören ihrem Besitzer (`data/sessions/`) und sind nach dem Login von
  jedem Gerät aus erreichbar
- Jeder Browser-Tab streamt seine eigenen Antworten; externe Identitäten (Telegram-ID,
  E-Mail-Adresse) werden über `data/user_mapping.json` AIfred-Benutzern zugeordnet

**Von allen geteilt:**
- Backend, Modelle und die Einstellungen in `data/settings.json` — ein Modellwechsel durch
  einen User gilt für alle
- Die GPUs: Anfragen werden nacheinander abgearbeitet

Laufende Anfragen sind sicher (ihre Parameter liegen bereits beim Backend); die
*nächste* Anfrage eines anderen Users nutzt die neuen Einstellungen. Änderungen an den Einstellungen erreichen
offene Tabs innerhalb von ein, zwei Sekunden; das Modell-Dropdown aktualisiert sich eventuell erst, wenn es
erneut geöffnet wird (Reflex-Einschränkung).

Das ist Absicht: Lokale Hardware betreibt einen Satz Modelle effizient, nicht einen
pro User. AIfred ist kein mandantenfähiger Dienst — keine Kontingente, keine Einstellungen
pro User, und jeder eingeloggte User kann globale Einstellungen ändern. Vergib Konten
nur an Menschen, denen du vertraust.

---

## Fehlerbehebung

**Dienst startet nicht**
```bash
journalctl -u aifred-intelligence -n 50
systemctl status llama-swap ollama
```

**Neustart-Button zeigt keine Wirkung** — prüfe die Polkit-Regel
(`/etc/polkit-1/rules.d/10-aifred.rules`, siehe [Einrichtungsanleitung](deployment.md#polkit-regel-neustart-ohne-sudo)).

**Modell fehlt, Skip-Liste, OOM** — siehe [Einrichtungsanleitung → Fehlerbehebung](deployment.md#12-fehlerbehebung).

**Debug-Log:** `tail -f data/logs/aifred_debug.log`
