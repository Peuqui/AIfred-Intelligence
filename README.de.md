**🌍 Sprachen:** [English](README.md) | [Deutsch](README.de.md)

---

<img src="assets/AIfred-Zylinder.png" alt="AIfred" width="80" align="left" style="margin-right: 16px;">

# AIfred Intelligence v3.1

**Autonomer KI-Assistent mit Tool Use, Message Hub, Multi-Agent-Debatten & lokaler LLM-Inferenz**

AIfred Intelligence ist ein vollwertiger KI-Assistent der lokal auf eigener Hardware läuft. Er verwaltet autonom E-Mails, Termine, Dokumente und Datenbanken — mit Function Calling, persistentem Gedächtnis und Multi-Agent-Debatten. Keine Cloud-Abhängigkeit, volle Datenhoheit.

**📺 [Beispiel-Showcases ansehen](https://peuqui.github.io/AIfred-Intelligence/)** - Exportierte Chats: Multi-Agent-Debatten, Chemie, Mathe, Coding und Web-Recherche.

---

## ✨ Features

### 🧠 Autonome Fähigkeiten (Function Calling / Tool Use)

Das LLM entscheidet autonom welche Tools es braucht — OpenAI-kompatible Tool-Infrastruktur mit Plugin-System:

- **Message Hub — AIfred als Kommunikationszentrale**: AIfred überwacht externe Kanäle und verarbeitet eingehende Nachrichten autonom. **Läuft headless** — kein Browser nötig. Channel-Plugins lauschen im Hintergrund, das LLM verarbeitet und antwortet über Discord/E-Mail eigenständig. Die Web-UI wird nur für die Ersteinrichtung (Credentials, Plugin-Toggles) und optionales Monitoring benötigt. **Einheitliches Plugin-System**: `.py`-Datei in `plugins/channels/` oder `plugins/tools/` ablegen — wird automatisch erkannt, keine Code-Änderungen nötig. **Eingebaute Kanäle**: E-Mail Monitor (IMAP IDLE Push-basiert + SMTP Auto-Reply), Discord (Bot mit Channel- + DM-Support, `/clear`-Befehl). **Plugin Manager** UI-Modal zum Ein-/Ausschalten aller Plugins zur Laufzeit (verschiebt Dateien nach `disabled/`). Pipeline: **Channel Listener** → **Envelope-Normalisierung** → **SQLite Routing Table** → **AIfred Engine-Aufruf** (mit vollem Toolkit inkl. Web-Recherche, Kalenderprüfung) → **Auto-Reply** (optional, per Toggle). Agent-Routing: Sokrates oder Salomo per Name ansprechen. **Hinweis**: Hub-Nachrichten werden ohne Browser-State verarbeitet — Fortschrittsanzeigen, Live-Streaming und Quellen-HTML stehen bei Hub-Nachrichten nicht zur Verfügung; dies ist systembedingt, keine Einschränkung. Siehe [Architektur & Setup](docs/plans/message-hub-architecture.md)
- **E-Mail-Integration**: E-Mails lesen, suchen und senden via IMAP/SMTP. Senden erfordert explizite Bestätigung (Entwurf → Prüfung → Bestätigung). Credentials über `.env` oder UI-Modal konfigurierbar
- **EPIM-Datenbank-Integration**: Voller CRUD-Zugriff auf die [EssentialPIM](https://www.essentialpim.com/) Firebird 2.5 Datenbank — das LLM sucht, erstellt, ändert und löscht eigenständig Kalendertermine, Kontakte, Notizen, Todos und Passworteinträge. Automatische Name-zu-ID-Auflösung, Anti-Halluzinations-Schutz, 7-Tage-Datumsreferenz
- **Workspace (Dateien & Dokumente)**: Dokumente hochladen (PDF, Word, Excel, PowerPoint, LibreOffice, TXT, MD, CSV), automatisches Chunking und Embedding in ChromaDB. Das LLM kann autonom Dateien durchsuchen, lesen (PDFs seitenweise), schreiben und bearbeiten — und dann in die Vektordatenbank einspeisen. Dokument-Manager mit Preview, Download und Löschen
- **Sandboxed Code-Ausführung**: LLM schreibt und führt Python-Code in isoliertem Subprocess aus. Unterstützt numpy, pandas, matplotlib, plotly, seaborn, scipy, sklearn. Interaktive HTML/JS-Visualisierungen (Plotly 3D, Canvas-Spiele, Simulationen) direkt im Chat
- **Agenten-Langzeitgedächtnis**: Persistentes Gedächtnis pro Agent via ChromaDB — Agenten speichern eigenständig Erkenntnisse, kombinierter Recall (10 neueste + semantische Suche), Session-Pinning. Memory-Browser zum Inspizieren und Aufräumen. Inkognito-Modus (🔒)
- **Automatische Web-Recherche**: KI entscheidet selbst wann Recherche nötig ist. Multi-API (Brave, Tavily, SearXNG) mit automatischem Scraping und Ranking. Semantischer Vector-Cache via ChromaDB
- Weitere Tools: `calculate` (Berechnungen), `web_fetch` (URLs abrufen), `store_memory` (Gedächtnis)
- **Plugin-Übersicht:** [Verfügbare Plugins](docs/de/guides/plugins-overview.md)

### 🎩 Multi-Agent-System

- **Multi-Agent Debate System**: AIfred + Sokrates + Salomo + Vision + unbegrenzt eigene Agenten
- **Benutzerdefinierte Agenten**: Name, Emoji, Rolle, zweisprachige Prompts (DE/EN), eigenes Langzeitgedächtnis. Agenten-Editor in der UI
- **5 Diskussionsmodi**: Standard, Kritische Prüfung, Auto-Konsens, Tribunal, Symposion
- **Direkte Ansprache**: Jeden Agenten per Name adressieren — auch über Telegram, Discord und E-Mail via Message Hub
- **User-Mapping**: Externe Identitäten (Telegram-ID, E-Mail-Adresse) werden auf AIfred-Benutzernamen gemappt (`data/user_mapping.json`) — AIfred erkennt dich über alle Kanäle
- **6-Schichten Prompt-System**: Identität + Reasoning + Multi-Agent + Aufgabe + Gedächtnis + Persönlichkeit

### ⚙️ LLM-Infrastruktur

- **Multi-Backend-Unterstützung**: llama.cpp via llama-swap (GGUF), Ollama (GGUF), vLLM (AWQ), TabbyAPI (EXL2), Cloud APIs (Qwen, DeepSeek, Claude)
- **Verteilte Inferenz (RPC)**: Modelle über mehrere Rechner im LAN verteilen via llama.cpp RPC
- **Automatische Kontext-Kalibrierung**: VRAM-bewusste Kontextgröße pro Backend mit Binary Search, RoPE-Skalierung, Tensor-Split-Optimierung
- **Denkmodus**: Chain-of-Thought-Reasoning (Qwen3, NemoTron, QwQ)
- **History-Kompression**: Intelligente Kompression bei 70% Context-Auslastung für unbegrenzte Konversationen
- **Automatisches Modell-Lifecycle**: Zero-Config — neue Modelle beim Start automatisch erkannt, entfernte bereinigt
- **Sampling-Parameter**: Per-Agent Temperature, Top-K, Top-P, Min-P, Repeat-Penalty (Auto/Manual)
- **Performance**: Direct-IO für schnelles Laden, Details in der [Modell-Parameter-Doku](docs/de/benchmarks/model-params.md)

### 🎤 Sprach- & Vision-Interface

- **Sprachschnittstelle**: STT (Whisper) und TTS (Edge TTS, XTTS v2 Voice Cloning, MOSS-TTS 1.7B, DashScope Qwen3-TTS Cloud-Streaming, Piper, espeak). Per-Agent TTS-Konfiguration (Stimme, Speed, Pitch, Ein/Aus pro Agent), nahtlose Echtzeit-Audioausgabe
- **Vision/OCR**: Bildanalyse mit multimodalen LLMs (DeepSeek-OCR, Qwen3-VL, Ministral-3), VL Follow-Up, interaktiver Bild-Zuschnitt, 2-Modell-Architektur (Vision-LLM + Haupt-LLM)

### 🔒 Security-Architektur

Mehrstufiges Sicherheitskonzept — Security wird im Framework erzwungen, nicht in Plugins:

- **5-Stufen Permission-System** (Tier 0–4): READONLY → COMMUNICATE → WRITE_DATA → WRITE_SYSTEM → ADMIN. Jedes Tool hat einen festen Tier, externe Kanäle bekommen einen maximalen Tier zugewiesen
- **Inbound Sanitization**: HTML-Strip, Zero-Width-Character-Entfernung, NFC-Normalisierung aller eingehenden Nachrichten
- **Delimiter Defense**: Externe Nachrichten werden in `<external_message>` Tags gewrapped mit Sender, Channel und Trust-Level
- **Security Boundary Prompt**: LLM wird instruiert, keine Anweisungen aus externen Nachrichten auszuführen
- **Rule of Two**: Write-Tier Tools von externen Channels blockiert (kein Datei-Löschen per E-Mail)
- **Rate Limiting**: Max Tool-Calls pro Zeitfenster pro Channel
- **Chain Depth Limit**: Max 10 Tool-Calls pro Request (verhindert Endlos-Schleifen)
- **Output Sanitization**: Secret-Patterns (API-Keys, Passwörter) werden aus Tool-Rückgaben entfernt
- **Credential Broker**: Plugins greifen nie direkt auf Secrets zu — nur über `broker.get()`
- **Audit-Log**: Jeder Tool-Aufruf wird protokolliert (Zeitstempel, Channel, Tool, Tier, Ergebnis)

> **Details:** [Security-Architektur](docs/de/architecture/security.md)

### 🖥️ UI & Session-Verwaltung

- **Zentrales Einstellungs-Modal** (☰ Hamburger-Menü): Agenten-Editor (Metadata, TTS, Prompts), Memory-Browser (pro Agent mit Type-Filter), Datenbank-Verwaltung (ChromaDB: Research Cache, Documents — durchsuchen, einzeln oder komplett löschen), Plugin Manager, Audit-Log
- **Benutzer-Authentifizierung**: Username + Passwort mit Whitelist-Registrierung
- **Session-Verwaltung**: Chat-Liste mit LLM-generierten Titeln, Session-Wechsel, persistente History
- **Chat teilen**: Export als portable HTML-Datei (KaTeX-Fonts inline, TTS-Audio eingebettet, offline-fähig)
- **LaTeX & Chemie**: KaTeX für Mathe-Formeln, mhchem für Chemie
- **HTML-Vorschau**: KI-generierter HTML-Code öffnet direkt im Browser
- **Harmony-Template Support**: GPT-OSS-120B mit offiziellem Harmony-Format

### 🎩 Multi-Agent Diskussionsmodi

AIfred unterstützt verschiedene Diskussionsmodi mit Sokrates (Kritiker) und Salomo (Richter):

| Modus | Ablauf | Wer entscheidet? |
|-------|--------|------------------|
| **Standard** | Beliebiger Agent antwortet (per Toggle wählbar) | — |
| **Kritische Prüfung** | AIfred → Sokrates (+ Pro/Contra) → STOP | User |
| **Auto-Konsens** | AIfred → Sokrates → Salomo (X Runden) | Salomo |
| **Tribunal** | AIfred ↔ Sokrates (X Runden) → Salomo | Salomo (Urteil) |
| **Symposion** | 2+ frei wählbare Agenten diskutieren (X Runden) | Kein Richter — Multiperspektive |

**Agenten:**
- 🎩 **AIfred** - Butler & Gelehrter - beantwortet Fragen (britischer Butler-Stil mit dezenter Noblesse)
- 🏛️ **Sokrates** - Kritischer Philosoph - hinterfragt & liefert Alternativen mit sokratischer Methode
- 👑 **Salomo** - Weiser Richter - synthetisiert Argumente und fällt finale Entscheidungen
- 📷 **Vision** - Bildanalyst - OCR und visuelle Q&A (erbt AIfred's Persönlichkeit)
- 🤖 **Eigene Agenten** - Benutzerdefinierte Agenten mit vollständiger Prompt-Anpassung

**Anpassbare Persönlichkeiten:**
- Alle Agenten-Prompts sind Textdateien in `prompts/de/` und `prompts/en/`
- Agenten-Konfiguration in `data/agents.json` — Prompt-Pfade, Toggles, Rollen
- Persönlichkeit kann in den UI-Einstellungen ein-/ausgeschaltet werden (behält Identität, entfernt Stil)
- 6-Schichten Prompt-System: Identität (wer) + Reasoning (wie denken) + Multi-Agent (wer sind die anderen) + Aufgabe (was) + Gedächtnis (Langzeit, Inkognito-fähig) + Persönlichkeit (wie sprechen)
- **Agenten-Editor**: Agenten erstellen, bearbeiten und löschen über die UI — DOM-basierte Eingaben, DE/EN Prompt-Bearbeitung, Emoji-Auswahl
- **Memory-Browser**: ChromaDB-Gedächtnis pro Agent inspizieren und verwalten (Session-Zusammenfassungen, Erkenntnisse, etc.)
- **Mehrsprachig**: Agenten antworten in der Sprache des Users (deutsche Prompts für Deutsch, englische Prompts für alle anderen Sprachen)

**Direkte Agenten-Ansprache**:
- Jeden Agenten direkt ansprechen: "Sokrates, was denkst du über...?" → Sokrates antwortet mit sokratischer Methode
- AIfred direkt ansprechen: "AIfred, erkläre..." → AIfred antwortet ohne Sokrates-Analyse
- Eigene Agenten über ID oder Anzeigename ansprechbar (automatisch per Intent-Erkennung)
- **Aktiver-Agent-Toggle**: Pill-Buttons zur Auswahl welcher Agent im Standard-Modus antwortet
- Unterstützt STT-Transkriptionsvarianten: "Alfred", "Eifred", "AI Fred"
- Funktioniert auch am Satzende: "Gut erklärt. Sokrates." / "Prima gemacht. Alfred!"

**Intelligentes Context-Handling** (v2.10.2):
- Multi-Agent-Nachrichten verwenden `role: system` mit `[MULTI-AGENT CONTEXT]` Prefix
- Speaker-Labels `[SOKRATES]:` und `[AIFRED]:` bleiben für LLM-Kontext erhalten
- Verhindert, dass LLM Agenten-Austausch mit eigenen Antworten verwechselt
- Alle Prompts erhalten automatisch aktuelles Datum/Uhrzeit für zeitbezogene Fragen

**Perspektiven-System** (v2.10.3):
- Jeder Agent sieht die Konversation aus seiner eigenen Perspektive
- Sokrates sieht AIfred's Antworten als `[AIFRED]:` (user role), seine eigenen als `assistant`
- AIfred sieht Sokrates' Kritik als `[SOKRATES]:` (user role), seine eigene als `assistant`
- Verhindert Identitätsverwechslung zwischen Agenten bei mehrrundigen Debatten

```
┌─────────────────────────────────────────┐
│          llm_history (gespeichert)      │
│                                         │
│  [AIFRED]: "Antwort 1"                  │
│  [SOKRATES]: "Kritik"                   │
│  [AIFRED]: "Antwort 2"                  │
└─────────────────────────────────────────┘
                    │
                    ▼
    ┌───────────────┼───────────────┐
    │               │               │
    ▼               ▼               ▼
┌─────────┐   ┌──────────┐   ┌─────────┐
│ AIfred  │   │ Sokrates │   │ Salomo  │
│ ruft an │   │ ruft an  │   │ ruft an │
└────┬────┘   └────┬─────┘   └────┬────┘
     │             │              │
     ▼             ▼              ▼
┌─────────┐   ┌──────────┐   ┌─────────┐
│assistant│   │  user    │   │  user   │
│"Antw 1" │   │[AIFRED]: │   │[AIFRED]:│
│  user   │   │assistant │   │  user   │
│[SOKR].. │   │"Kritik"  │   │[SOKR].. │
│assistant│   │  user    │   │  user   │
│"Antw 2" │   │[AIFRED]: │   │[AIFRED]:│
└─────────┘   └──────────┘   └─────────┘

Eine Quelle, drei Sichten - je nachdem wer gerade spricht.
Eigene Nachrichten = assistant (ohne Label), andere = user (mit Label).
```

**Strukturierte Kritik-Prompts** (v2.10.3):
- Rundennummer-Platzhalter `{round_num}` - Sokrates weiß welche Runde es ist
- Maximal 1-2 Kritikpunkte pro Runde
- Sokrates kritisiert nur - entscheidet nie über Konsens (das ist Salomos Aufgabe)

**Temperatursteuerung** (v2.10.4):
- Auto-Modus: Intent-Detection bestimmt Basis-Temperatur (FACTUAL=0.2, MIXED=0.5, CREATIVE=1.1)
- Manual-Modus: Per-Agent Temperatur in der Sampling-Tabelle
- Konfigurierbarer Sokrates-Offset im Auto-Modus (Standard +0.2, max 1.0)
- Alle Temperatur-Einstellungen im "LLM Parameters (Advanced)" Collapsible

**Sampling-Parameter-Persistenz:**
- **Temperature**: Wird in `settings.json` gespeichert (pro Agent, überlebt Neustart)
- **Top-K, Top-P, Min-P, Repeat-Penalty**: NICHT gespeichert — werden bei jedem Neustart auf modellspezifische Defaults aus der llama-swap YAML-Config zurückgesetzt
- **Modellwechsel**: Setzt ALLE Sampling-Parameter (inkl. Temperature) auf YAML-Defaults zurück
- **Reset-Button (↺)**: Setzt ALLE Sampling-Parameter (inkl. Temperature) auf YAML-Defaults zurück

**Trialog-Workflow (Auto-Konsens mit Salomo):**
```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────────┐
│   User      │────▶│   🎩 AIfred     │────▶│   🏛️ Sokrates       │
│   Frage     │     │   THESE         │     │   ANTITHESE         │
└─────────────┘     │   (Antwort)     │     │   (Kritik)          │
                    └─────────────────┘     └──────────┬──────────┘
                                                       │
                              ┌─────────────────────────┘
                              ▼
                    ┌─────────────────────┐
                    │   👑 Salomo         │
                    │   SYNTHESE          │
                    │   (Vermittlung)     │
                    └──────────┬──────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
          ┌────────┐                     ┌────────┐
          │  LGTM  │                     │ Weiter │
          │ Fertig │                     │ Runde  │
          └────────┘                     └────────┘
```

**Tribunal-Workflow:**
```
┌─────────────┐     ┌─────────────────────────────────────┐
│   User      │────▶│   🎩 AIfred ↔ 🏛️ Sokrates          │
│   Frage     │     │   Debatte für X Runden              │
└─────────────┘     └──────────────────┬──────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────┐
                    │   👑 Salomo - Finales Urteil        │
                    │   Wägt beide Seiten, entscheidet    │
                    └─────────────────────────────────────┘
```

**Message-Anzeige-Format:**

Jede Nachricht wird einzeln mit ihrem Emoji und Mode-Label angezeigt:

| Rolle | Agent | Anzeigeformat | Beispiel |
|-------|-------|---------------|----------|
| **User** | — | 🙋 {Username} (rechtsbündig) | 🙋 User: "Was ist Python?" |
| **Assistant** | `aifred` | 🎩 AIfred [{Modus} R{N}] (linksbündig) | 🎩 AIfred [Auto-Konsens: Überarbeitung R2] |
| **Assistant** | `sokrates` | 🏛️ Sokrates [{Modus} R{N}] (linksbündig) | 🏛️ Sokrates [Tribunal: Kritik R1] |
| **Assistant** | `salomo` | 👑 Salomo [{Modus} R{N}] (linksbündig) | 👑 Salomo [Tribunal: Urteil R3] |
| **System** | — | 📊 Zusammenfassung (ausklappbar inline) | 📊 Zusammenfassung #1 (5 Nachrichten) |

**Mode-Labels:**
- Standard-Antworten: Kein Label (klare Anzeige)
- Multi-Agent-Modi: `[{Modus}: {Aktion} R{N}]` Format
  - Modus: `Auto-Konsens`, `Tribunal`, `Kritische Prüfung`
  - Aktion: `Überarbeitung`, `Kritik`, `Synthese`, `Urteil`
  - Runde: `R1`, `R2`, `R3`, etc.

**Beispiele:**
- Standard: `🎩 AIfred` (kein Label)
- Auto-Konsens R1: `🎩 AIfred [Auto-Konsens: Überarbeitung R1]`
- Tribunal R2: `🏛️ Sokrates [Tribunal: Kritik R2]`
- Finales Urteil: `👑 Salomo [Tribunal: Urteil R3]`

**Prompt-Dateien pro Modus:**
| Modus | Agent | Prompt-Datei | Mode-Label | Anzeige-Beispiel |
|-------|-------|--------------|------------|------------------|
| **Standard** | AIfred | `aifred/system_rag` oder `system_minimal` | — | 🎩 AIfred |
| **Direkt AIfred** | AIfred | `aifred/direct` | Direkte Antwort | 🎩 AIfred [Direkte Antwort] |
| **Direkt Sokrates** | Sokrates | `sokrates/direct` | Direkte Antwort | 🏛️ Sokrates [Direkte Antwort] |
| **Kritische Prüfung** | Sokrates | `sokrates/critic` | Kritische Prüfung | 🏛️ Sokrates [Kritische Prüfung] |
| **Kritische Prüfung** | AIfred | `aifred/system_minimal` | Kritische Prüfung: Überarbeitung | 🎩 AIfred [Kritische Prüfung: Überarbeitung] |
| **Auto-Konsens** R{N} | Sokrates | `sokrates/critic` | Auto-Konsens: Kritik R{N} | 🏛️ Sokrates [Auto-Konsens: Kritik R2] |
| **Auto-Konsens** R{N} | AIfred | `aifred/system_minimal` | Auto-Konsens: Überarbeitung R{N} | 🎩 AIfred [Auto-Konsens: Überarbeitung R2] |
| **Auto-Konsens** R{N} | Salomo | `salomo/mediator` | Auto-Konsens: Synthese R{N} | 👑 Salomo [Auto-Konsens: Synthese R2] |
| **Tribunal** R{N} | Sokrates | `sokrates/tribunal` | Tribunal: Angriff R{N} | 🏛️ Sokrates [Tribunal: Angriff R1] |
| **Tribunal** R{N} | AIfred | `aifred/defense` | Tribunal: Verteidigung R{N} | 🎩 AIfred [Tribunal: Verteidigung R1] |
| **Tribunal** Final | Salomo | `salomo/judge` | Tribunal: Urteil R{N} | 👑 Salomo [Tribunal: Urteil R3] |

**Hinweis:** Alle Prompts sind in `prompts/de/` (Deutsch) und `prompts/en/` (Englisch)

**UI-Einstellungen:**
- Sokrates-LLM und Salomo-LLM separat wählbar (können verschiedene Modelle sein)
- Max. Debattenrunden (1-10, Standard: 3)
- Diskussionsmodus im Settings-Panel
- 💡 Hilfe-Icon öffnet Modal mit Übersicht aller Modi

**Thinking-Support:**
- Alle Agenten (AIfred, Sokrates, Salomo) unterstützen Thinking-Mode
- `<think>`-Blöcke werden als Collapsible formatiert

### 🔧 Technische Highlights
- **Reflex-Framework**: React-Frontend aus Python generiert
- **WebSocket-Streaming**: Echtzeit-Updates ohne Polling
- **Adaptive Temperatur**: KI wählt Temperatur basierend auf Fragetyp
- **Token-Management**: Dynamische Context-Window-Berechnung
- **VRAM-bewusster Kontext**: Automatische Kontext-Größe basierend auf verfügbarem GPU-Speicher
- **Debug-Konsole**: Umfangreiches Logging und Monitoring
- **ChromaDB-Server-Modus**: Thread-sichere Vector-DB via Docker (0.0 Distance für exakte Matches)
- **GPU-Erkennung**: Automatische Erkennung und Warnung bei inkompatiblen Backend-GPU-Kombinationen ([docs/GPU_COMPATIBILITY.md](docs/GPU_COMPATIBILITY.md))
- **Kontext-Kalibrierung**: Intelligente Kalibrierung pro Modell für Ollama und llama.cpp
  - **Ollama**: Binäre Suche mit automatischer VRAM/Hybrid-Modus-Erkennung (512 Token Präzision, 3 GB RAM-Reserve)
  - **llama.cpp** (3-phasige Kalibrierung für Multi-GPU-Setups):
    - **Phase 1** (GPU-only): Binäre Suche auf `-c` mit `ngl=99`, stoppt llama-swap, testet auf Temp-Port
      - KV-Fallback-Chain: f16 → q8_0 (wenn < nativer Kontext) → q4_0 (letzter Ausweg, nur wenn q8_0 < 32K)
      - Small-Model-Shortcut: Modelle mit `native_context ≤ 8192` werden direkt getestet (keine Binärsuche)
      - flash-attn-Auto-Erkennung: Startfehler → automatischer Neuversuch ohne `--flash-attn`, aktualisiert llama-swap YAML bei Erfolg
    - **Phase 2** (Speed-Variante): Min-GPU-Strategie — berechnet minimale GPU-Anzahl für Modell-Gewichte, weniger GPU-Grenzen = weniger Transfer-Overhead = schnellere Inferenz (Tradeoff: reduzierter max. Kontext). Eigene KV-Chain (f16 → q8_0), unabhängig von Phase 1. Erstellt separaten `modell-speed`-Eintrag in llama-swap YAML mit eigenem KV-Quant
    - **Phase 3** (Hybrid-Fallback): Wenn Phase 1 < 32K → NGL-Reduzierung um VRAM für KV-Cache freizumachen. Erbt KV-Quantisierung von Phase 1
    - Startfehler (unbekannte Architektur, falsche CUDA-Version) werden geloggt und nie als falsche Kalibrierungsdaten gespeichert
  - Ergebnisse in einheitlichem `data/model_vram_cache.json` gespeichert
- **llama-swap Autoscan**: Automatische Modell-Erkennung beim Service-Start (`scripts/llama-swap-autoscan.py`) — **kein manuelles YAML-Editieren nötig**
  - Scannt Ollama-Manifests → erstellt beschreibende Symlinks in `~/models/` (z.B. `sha256-6335adf...` → `Qwen3-14B-Q8_0.gguf`)
  - Scannt HuggingFace-Cache (`~/.cache/huggingface/hub/`) → erstellt Symlinks für heruntergeladene GGUFs
  - VL-Modelle (mit passendem `mmproj-*.gguf`) erhalten automatisch das `--mmproj`-Argument
  - **Kompatibilitätsprüfung**: Jedes neue Modell wird kurz mit llama-server gestartet — nicht unterstützte Architekturen (z.B. `deepseekocr`) werden erkannt und nicht in die Config aufgenommen
  - **Skip-Liste** (`~/.config/llama-swap/autoscan-skip.json`): Inkompatible Modelle werden gespeichert und nicht bei jedem Neustart erneut geprüft. Eintrag löschen, um nach einem llama.cpp-Update erneut zu testen
  - Erkennt neue GGUFs und erstellt llama-swap Config-Einträge mit optimalen Defaults (`-ngl 99`, `--flash-attn on`, `-ctk q8_0`, etc.)
  - Pflegt `groups.main.members` in der YAML automatisch — alle Modelle teilen VRAM-Exklusivität ohne manuelles Editieren
  - Erstellt vorläufige VRAM-Cache-Einträge (Kalibrierung über die UI speichert `vram_used_mb` während das Modell geladen ist)
  - Erstellt `config.yaml` von Grund auf falls nicht vorhanden — kein manuelles Bootstrap nötig
  - Läuft als `ExecStartPre` im systemd-Service → `ollama pull model` oder `hf download` genügt, um ein Modell hinzuzufügen
- **Ctx/Speed-Schalter**: Pro-Agenten-Toggle zwischen zwei vorkalibrierten Varianten (Ctx = maximaler Kontext, ⚡ Speed = 32K + aggressive GPU-Lastverteilung)
- **Parallele Web-Suche**: 2-3 optimierte Queries parallel auf APIs verteilt (Tavily, Brave, SearXNG), automatische URL-Deduplizierung, optionales self-hosted SearXNG
- **Paralleles Scraping**: ThreadPoolExecutor scrapt 3-7 URLs gleichzeitig, erste erfolgreiche Ergebnisse werden verwendet
- **Nicht-verfügbare Quellen**: Zeigt nicht scrapbare URLs mit Fehlergrund an (Cloudflare, 404, Timeout) - im Vector Cache gespeichert für Cache-Hits
- **PDF-Unterstützung**: Direkte Extraktion aus PDF-Dokumenten (AWMF-Leitlinien, PubMed PDFs) via PyMuPDF mit Browser-User-Agent

### 🔊 Sprachschnittstelle (TTS-Engines)

AIfred unterstützt 6 TTS-Engines mit unterschiedlichen Trade-offs zwischen Qualität, Latenz und Ressourcenverbrauch. Jede Engine wurde nach intensivem Ausprobieren für einen bestimmten Anwendungsfall gewählt.

| Engine | Typ | Streaming | Qualität | Latenz | Ressourcen |
|--------|-----|-----------|----------|--------|------------|
| **XTTS v2** | Lokal (Docker) | Satzweise | Hoch (Voice Cloning) | ~1-2s/Satz | ~2 GB VRAM |
| **MOSS-TTS 1.7B** | Lokal (Docker) | Keins (Batch nach Bubble) | Exzellent (bestes Open-Source) | ~18-22s/Satz | ~11,5 GB VRAM |
| **DashScope Qwen3-TTS** | Cloud (API) | Satzweise | Hoch (Voice Cloning) | ~1-2s/Satz | Nur API-Key |
| **Piper TTS** | Lokal | Satzweise | Mittel | <100ms | Nur CPU |
| **eSpeak** | Lokal | Satzweise | Niedrig (robotisch) | <50ms | Nur CPU |
| **Edge TTS** | Cloud | Satzweise | Gut | ~200ms | Nur Internet |

**Warum mehrere Engines?**

Die Suche nach der perfekten TTS-Erfahrung führte durch mehrere Iterationen:

- **Edge TTS** war die erste Engine -- kostenlos, schnell, ordentliche Qualität, aber begrenzte Stimmen und kein Voice Cloning.
- **XTTS v2** brachte hochwertiges Voice Cloning mit mehrsprachiger Unterstützung. Satzweises Streaming funktioniert gut: Während das LLM den nächsten Satz generiert, synthetisiert XTTS den aktuellen. Benötigt allerdings einen Docker-Container und ~2 GB VRAM.
- **MOSS-TTS 1.7B** liefert die beste Sprachqualität aller Open-Source-Modelle (SIM 73-79%), aber zu einem Preis: ~18-22 Sekunden pro Satz macht es ungeeignet für Streaming. Audio wird als Batch nach der vollständigen Antwort generiert -- akzeptabel für kurze Antworten, aber frustrierend bei längeren.
- **DashScope Qwen3-TTS** ergänzt cloudbasiertes Voice Cloning über Alibaba Clouds API. Standardmäßig wird satzweises Streaming verwendet (wie XTTS), was bessere Intonation liefert. Ein Echtzeit-WebSocket-Modus (wortweise Chunks, ~200ms erster Audio-Chunk) ist ebenfalls implementiert, aber standardmäßig deaktiviert -- er tauscht etwas schlechtere Prosodie gegen schnelleres erstes Audio. Zum Reaktivieren den WebSocket-Block in `state.py:_init_streaming_tts()` auskommentieren (siehe Code-Kommentar dort).
- **Piper TTS** und **eSpeak** dienen als leichtgewichtige Offline-Alternativen, die ohne Docker, GPU oder Internetverbindung funktionieren.

**Wiedergabe-Architektur:**
- Sichtbares HTML5 `<audio>`-Widget mit Blob-URL-Prefetching (nächste 2 Chunks werden als Blobs in den Speicher vorgeladen)
- `preservesPitch: true` für Geschwindigkeitsanpassungen ohne Chipmunk-Effekt
- Agentenspezifische Stimme/Tonhöhe/Geschwindigkeit (AIfred, Sokrates, Salomo können jeweils eigene Stimmen haben)
- SSE-basiertes Audio-Streaming vom Backend zum Browser (persistente Verbindung, 15s Keepalive)

### ⚠️ Modell-Empfehlungen
- **Automatik-LLM** (Intent-Erkennung, Query-Optimierung, Adressaten-Erkennung): Mittlere Instruct-Modelle empfohlen
  - **Empfohlen**: `qwen3:14b` (Q4 oder Q8 Quantisierung)
  - Besseres semantisches Verständnis für komplexe Adressaten-Erkennung ("Was denkt Alfred über Salomos Antwort?")
  - Kleine 4B-Modelle können bei nuancierten Satzsemantiken Schwierigkeiten haben
  - Thinking-Modus wird automatisch für Automatik-Aufgaben deaktiviert (schnelle Entscheidungen)
  - **„(wie AIfred-LLM)"**-Option verfügbar – nutzt dasselbe Modell wie AIfred ohne zusätzlichen VRAM
- **Haupt-LLM**: Größere Modelle (14B+, idealerweise 30B+) für besseres Kontextverständnis und Prompt-Following
  - Sowohl Instruct- als auch Thinking-Modelle funktionieren gut
  - "Denkmodus" für Chain-of-Thought-Reasoning bei komplexen Aufgaben aktivieren
  - **Sprach-Hinweis**: Kleine Modelle (4B-14B) antworten möglicherweise auf Englisch, wenn der RAG-Kontext überwiegend englische Web-Inhalte enthält - auch bei deutschen Prompts. Modelle ab 30B+ befolgen Sprachanweisungen zuverlässig, unabhängig von der Kontext-Sprache.

---

## 🔄 Research Mode Workflows

AIfred bietet 4 verschiedene Research-Modi, die je nach Anforderung unterschiedliche Strategien verwenden. Hier ist der detaillierte Ablauf jedes Modus:

### 📊 LLM Calls Übersicht

| Modus | Min LLM Calls | Max LLM Calls | Typische Dauer |
|-------|---------------|---------------|----------------|
| **Eigenes Wissen** | 1 | 1 | 5-30s |
| **Automatik** (Cache Hit) | 0 | 0 | <1s |
| **Automatik** (Direct Answer) | 2 | 3 | 5-35s |
| **Automatik** (Web Research) | 4 | 5 | 15-60s |
| **Websuche Schnell** | 3 | 4 | 10-40s |
| **Websuche Ausführlich** | 3 | 4 | 15-60s |

---

### 🔄 Pre-Processing (alle Modi)

**Gemeinsamer erster Schritt** für alle Research-Modi:

```
Intent + Addressee Detection
├─ LLM Call (Automatik-LLM) - kombiniert in einem Call
├─ Prompt: intent_detection
├─ Response: "FAKTISCH|sokrates" | "KREATIV|" | "GEMISCHT|aifred"
├─ Temperature-Nutzung:
│  ├─ Auto-Mode: FAKTISCH=0.2, GEMISCHT=0.5, KREATIV=1.0
│  └─ Manual-Mode: Intent ignoriert, manueller Wert verwendet
└─ Addressee: Direkte Agenten-Ansprache (sokrates/aifred/salomo)
```

Bei direkter Agenten-Ansprache wird der entsprechende Agent sofort aktiviert, unabhängig vom gewählten Research-Modus oder Temperature-Setting.

---

### 1️⃣ Eigenes Wissen Mode (Direct LLM)

**Einfachster Modus**: Direkter LLM-Aufruf ohne Web-Recherche oder KI-Entscheidung.

**Workflow:**
```
1. Message Building
   └─ Build from chat history
   └─ Inject system_minimal prompt (mit Timestamp)

2. Model Preloading (Ollama only)
   └─ backend.preload_model() - misst echte Ladezeit
   └─ vLLM/TabbyAPI: Skip (bereits in VRAM)

3. Token Management
   └─ estimate_tokens(messages, model_name)
   └─ calculate_dynamic_num_ctx()

4. LLM Call - Main Response
   ├─ Model: Haupt-LLM (z.B. Qwen2.5-32B)
   ├─ Temperature: Manual (User-Einstellung)
   ├─ Streaming: Ja (Echtzeit-Updates)
   └─ TTFT + Tokens/s Messung

5. Format & Save
   └─ format_thinking_process() für <think> Tags
   └─ Update chat history

6. History Compression (PRE-MESSAGE Check - VOR jedem LLM-Aufruf)
   ├─ Trigger: 70% Auslastung des kleinsten Context-Fensters
   │  └─ Multi-Agent: min_ctx aller Agenten wird verwendet
   ├─ Dual History: chat_history (UI) + llm_history (LLM, FIFO)
   └─ Summaries erscheinen inline im Chat wo komprimiert wurde
```

**LLM Calls:** 1 Haupt-LLM + optional 1 Compression-LLM (bei >70% Context)
**Async Tasks:** Keine
**Code:** `aifred/state.py` Lines 974-1117

---

### 2️⃣ Automatik Mode (AI Decision System)

**Intelligentester Modus**: KI entscheidet selbst, ob Web-Recherche nötig ist.

#### Phase 1: Vector Cache Check
```
1. Query ChromaDB für ähnliche Fragen
   └─ Distance < 0.5: HIGH Confidence → Cache Hit
   └─ Distance ≥ 0.5: CACHE_MISS → Weiter

2. IF CACHE HIT:
   └─ Antwort direkt aus Cache
   └─ RETURN (0 LLM Calls!)
```

#### Phase 2: RAG Context Check
```
1. Query cache für RAG candidates (distance 0.5-1.2)

2. FOR EACH candidate:
   ├─ LLM Relevance Check (Automatik-LLM)
   │  └─ Prompt: rag_relevance_check
   │  └─ Options: temp=0.1, num_ctx=AUTOMATIK_LLM_NUM_CTX
   └─ Keep if relevant

3. Build formatted context from relevant entries
```

#### Phase 3: Keyword Override Check
```
1. Check für explicit research keywords:
   └─ "recherchiere", "suche im internet", "google", etc.

2. IF keyword found:
   └─ Trigger fresh web research (mode='deep')
   └─ BYPASS Automatik decision
```

#### Phase 4: Automatik Decision (Kombinierter LLM-Call)
```
1. LLM Call - Research Decision + Query Generation (kombiniert)
   ├─ Model: Automatik-LLM (z.B. Qwen3:4B)
   ├─ Prompt: research_decision.txt
   │  ├─ Enthält: Aktuelles Datum (für zeitbezogene Queries)
   │  ├─ Vision-Kontext bei angehängten Bildern
   │  └─ Strukturierte JSON-Ausgabe
   ├─ Messages: KEINE History (fokussierte, unvoreingenommene Entscheidung)
   ├─ Options:
   │  ├─ temperature: 0.2 (konsistente Entscheidungen)
   │  ├─ num_ctx: 12288 (AUTOMATIK_LLM_NUM_CTX) - nur wenn Automatik ≠ AIfred-Modell
   │  ├─ num_predict: 256
   │  └─ enable_thinking: False (schnell)
   └─ Response: {"web": true, "queries": ["EN query", "DE query 1", "DE query 2"]}
              ODER {"web": false}

2. Query-Regeln (bei web=true):
   ├─ Query 1: IMMER auf Englisch (internationale Quellen)
   ├─ Query 2-3: In der Sprache der Frage
   └─ Jede Query: 4-8 Keywords

3. Parse decision:
   ├─ IF web=true: → Web Research mit vorgenerierten Queries
   └─ IF web=false: → Direct LLM Answer (Phase 5)
```

#### Phase 5: Direct LLM Answer (if decision = no)
```
1. Model Preloading (Ollama only)

2. Build Messages
   ├─ From chat history
   ├─ Inject system_minimal prompt
   └─ Optional: Inject RAG context (if found in Phase 2)

3. LLM Call - Main Response
   ├─ Model: Haupt-LLM
   ├─ Temperature: From Pre-Processing or manual
   ├─ Streaming: Ja
   └─ TTFT + Tokens/s Messung

4. Format & Update History
   └─ Metadata: "Cache+LLM (RAG)" or "LLM"

5. History Compression Check (wie in Eigenes Wissen Mode)
   └─ Automatische Kompression bei >70% Context-Auslastung
```

**LLM Calls:**
- Cache Hit: 0 + optional 1 Compression
- RAG Context: 2-6 + optional 1 Compression
- Web Research: 4-5 + optional 1 Compression
- Direct Answer: 2-3 + optional 1 Compression

**Code:** `aifred/lib/conversation_handler.py`

---

### 3️⃣ Websuche Schnell Mode (Quick Research)

**Schnellster Web-Research Modus**: Top 3 URLs, optimiert für Speed.

#### Phase 1: Session Cache Check
```
1. Check session-based cache
   └─ IF cache hit: Use cached sources → Skip to Phase 4
   └─ IF miss: Continue to Phase 2
```

#### Phase 2: Query Optimization + Web Search
```
1. LLM Call - Query Optimization
   ├─ Model: Automatik-LLM
   ├─ Prompt: query_optimization
   ├─ Messages: Last 3 history turns (for follow-up context)
   ├─ Options:
   │  ├─ temperature: 0.3 (balanced for keywords)
   │  ├─ num_ctx: min(8192, automatik_limit)
   │  └─ enable_thinking: False
   ├─ Post-processing:
   │  ├─ Extract <think> tags (reasoning)
   │  ├─ Clean query (remove quotes)
   │  └─ Add temporal context (current year)
   └─ Output: optimized_query, query_reasoning

2. Web Search (Multi-API with Fallback)
   ├─ Try: Brave API
   ├─ Fallback: Tavily
   ├─ Fallback: SearXNG (local)
   └─ Deduplication across APIs
```

#### Phase 2.5: URL-Filterung + LLM-basiertes Ranking (v2.15.30)
```
1. Non-Scrapable Domain Filter (VOR URL-Ranking)
   ├─ Konfig: data/blocked_domains.txt (leicht editierbar, eine Domain pro Zeile)
   ├─ Filtert Video-Plattformen: YouTube, Vimeo, TikTok, Twitch, Rumble, etc.
   ├─ Filtert Social Media: Twitter/X, Facebook, Instagram, LinkedIn
   ├─ Grund: Diese Seiten können nicht effektiv gescraped werden
   ├─ Debug-Log: "🚫 Blocked: https://youtube.com/..."
   └─ Zusammenfassung: "🚫 Filtered 6 non-scrapable URLs (video/social platforms)"

2. URL-Ranking (Automatik-LLM)
   ├─ Input: ~22 URLs (nach Filterung) mit Titeln und Snippets
   ├─ Model: Automatik-LLM (num_ctx: 12K)
   ├─ Prompt: url_ranking.txt (nur EN - Output ist numerisch)
   ├─ Options:
   │  ├─ temperature: 0.0 (deterministisches Ranking)
   │  └─ num_predict: 100 (kurze Antwort)
   ├─ Output: "3,7,1,12,5,8,2" (komma-getrennte Indizes)
   └─ Ergebnis: Top 7 (deep) oder Top 3 (quick) URLs nach Relevanz

3. Warum LLM-basiertes Ranking?
   ├─ Semantisches Verständnis der Query-URL-Relevanz
   ├─ Keine Wartung von Keyword-Listen oder Domain-Whitelists
   ├─ Passt sich jedem Thema an (universell)
   └─ Besser als first-come-first-served Reihenfolge

4. Skip-Bedingungen:
   ├─ Direct-URL-Modus (User hat URLs direkt angegeben)
   ├─ Weniger als top_n URLs gefunden
   └─ Keine Titel/Snippets verfügbar (Fallback auf ursprüngliche Reihenfolge)
```

#### Phase 3: Parallel Web Scraping
```
PARALLEL EXECUTION:
├─ ThreadPoolExecutor (max 5 workers)
│  └─ Scrape Top 3/7 URLs (nach Relevanz gerankt)
│     └─ Extract text content + word count
│
└─ Async Task: Main LLM Preload (Ollama only)
   └─ llm_client.preload_model(model)
   └─ Runs parallel to scraping
   └─ vLLM/TabbyAPI: Skip (already loaded)

Progress Updates:
└─ Yield after each URL completion
```

**Scraping-Strategie (trafilatura + Playwright Fallback):**
```
1. trafilatura (schnell, leichtgewichtig)
   └─ Direkter HTTP-Request, HTML-Parsing
   └─ Funktioniert für die meisten statischen Websites

2. WENN trafilatura < 800 Wörter liefert:
   └─ Playwright-Fallback (Headless Chromium)
   └─ Führt JavaScript aus, rendert dynamische Inhalte
   └─ Für SPAs: React, Vue, Angular Seiten

3. WENN Download fehlschlägt (404, Timeout, Bot-Schutz):
   └─ KEIN Playwright-Fallback (sinnlos)
   └─ URL als fehlgeschlagen markieren mit Fehlergrund
```

Der 800-Wörter-Schwellenwert ist konfigurierbar via `PLAYWRIGHT_FALLBACK_THRESHOLD` in `config.py`.

#### Phase 4: Context Building + LLM Response
```
1. Build Context
   ├─ Filter successful scrapes (word_count > 0)
   ├─ build_context() - smart token limit aware
   └─ Build system_rag prompt (with context + timestamp)

2. LLM Call - Final Response
   ├─ Model: Haupt-LLM
   ├─ Temperature: From Pre-Processing or manual
   ├─ Context: ~3 sources, 5K-10K tokens
   ├─ Streaming: Ja
   └─ TTFT + Tokens/s Messung

3. Cache-Entscheidung (via Volatility-Tag vom Haupt-LLM)
   ├─ Haupt-LLM inkludiert <volatility>DAILY/WEEKLY/MONTHLY/PERMANENT</volatility>
   ├─ Volatility bestimmt TTL:
   │  ├─ DAILY (24h): News, aktuelle Ereignisse
   │  ├─ WEEKLY (7d): Semi-aktuelle Themen
   │  ├─ MONTHLY (30d): Statistiken, Reports
   │  └─ PERMANENT (∞): Zeitlose Fakten ("Was ist Python?")
   ├─ Semantic Duplicate Check (distance < 0.3 zu existierenden Einträgen)
   │  └─ IF duplicate: Lösche alten Eintrag (garantiert neueste Daten)
   ├─ cache.add(query, answer, sources, metadata, ttl)
   └─ Debug: "💾 Antwort gecacht (TTL: {volatility})"

4. Format & Update History
   └─ Metadata: "(Agent: quick, {n} Quellen)"

5. History Compression Check (wie in Eigenes Wissen Mode)
   └─ Automatische Kompression bei >70% Context-Auslastung
```

**LLM Calls:**
- With Cache: 1-2 + optional 1 Compression
- Without Cache: 3-4 + optional 1 Compression

**Async Tasks:**
- Parallel URL scraping (3 URLs)
- Background LLM preload (Ollama only)

**Code:** `aifred/lib/research/orchestrator.py` + Submodules

---

### 4️⃣ Websuche Ausführlich Mode (Deep Research)

**Gründlichster Modus**: Top 7 URLs für maximale Informationstiefe.

**Workflow:** Identisch zu Websuche Schnell, mit folgenden Unterschieden:

#### Scraping Strategy
```
Quick Mode:  3 URLs → ~3 successful sources
Deep Mode:   7 URLs → ~5-7 successful sources

Parallel Execution:
├─ ThreadPoolExecutor (max 5 workers)
│  └─ Scrape Top 7 URLs simultaneously
│  └─ Continue until 5 successful OR all tried
│
└─ Async: Main LLM Preload (parallel)
```

#### Context Size
```
Quick: ~5K-10K tokens context
Deep:  ~10K-20K tokens context

→ Mehr Quellen = reicherer Kontext
→ Längere LLM Inference (10-40s vs 5-30s)
```

**LLM Calls:** Identisch zu Quick (3-4 + optional 1 Compression)
**Async Tasks:** Mehr URLs parallel (7 vs 3)
**Trade-off:** Höhere Qualität vs längere Dauer
**History Compression:** Wie alle Modi - automatisch bei >70% Context

---

### 🔀 Decision Flow Diagram

```
USER INPUT
    │
    ▼
┌─────────────────────┐
│ Research Mode?      │
└─────────────────────┘
    │
    ├── "none" ────────────────────────┐
    │                                   │
    ├── "automatik" ──────────────┐   │
    │                              │   │
    ├── "quick" ──────────────┐  │   │
    │                          │  │   │
    └── "deep" ────────────┐  │  │   │
                           │  │  │   │
                           ▼  ▼  ▼   ▼
                      ╔═══════════════════╗
                      ║ MODE HANDLER      ║
                      ╚═══════════════════╝
                               │
     ┌─────────────────────────┼──────────────────────┐
     │                         │                      │
     ▼                         ▼                      ▼
┌──────────┐         ┌──────────────┐       ┌─────────────┐
│ EIGENES  │         │ AUTOMATIK    │       │ WEB         │
│ WISSEN   │         │ (AI Decides) │       │ RESEARCH    │
└──────────┘         └──────────────┘       │ (quick/deep)│
     │                       │               └─────────────┘
     │                       ▼                      │
     │              ┌────────────────┐              │
     │              │ Vector Cache   │              │
     │              │ Check          │              │
     │              └────────────────┘              │
     │                       │                      │
     │          ┌────────────┼─────────────┐        │
     │          │            │             │        │
     │          ▼            ▼             ▼        │
     │     ┌────────┐  ┌─────────┐  ┌─────────┐   │
     │     │ CACHE  │  │ RAG     │  │ CACHE   │   │
     │     │ HIT    │  │ CONTEXT │  │ MISS    │   │
     │     │ RETURN │  │ FOUND   │  │         │   │
     │     └────────┘  └─────────┘  └─────────┘   │
     │                       │            │         │
     │                       │            ▼         │
     │                       │    ┌──────────────┐ │
     │                       │    │ Keyword      │ │
     │                       │    │ Override?    │ │
     │                       │    └──────────────┘ │
     │                       │         │     │      │
     │                       │         NO   YES     │
     │                       │         │     │      │
     │                       │         │     └──────┤
     │                       │         ▼            │
     │                       │   ┌──────────────┐  │
     │                       │   │ LLM Decision │  │
     │                       │   │ (yes/no)     │  │
     │                       │   └──────────────┘  │
     │                       │         │     │      │
     │                       │         NO   YES     │
     │                       │         │     │      │
     │                       │         │     └──────┤
     ▼                       ▼         ▼            ▼
╔══════════════════════════════════════════════════════╗
║         DIRECT LLM INFERENCE                         ║
║  1. Build Messages (with/without RAG)                ║
║  2. Intent Detection (auto mode)                     ║
║  3. Main LLM Call (streaming)                        ║
║  4. Format & Update History                          ║
╚══════════════════════════════════════════════════════╝
                           │
                           ▼
                    ┌──────────┐
                    │ RESPONSE │
                    └──────────┘

         WEB RESEARCH PIPELINE
         ═════════════════════
                    │
                    ▼
        ┌───────────────────┐
        │ Session Cache?    │
        └───────────────────┘
                    │
        ┌───────────┴────────────┐
        │                        │
        ▼                        ▼
   ┌────────┐          ┌─────────────────┐
   │ CACHE  │          │ Query           │
   │ HIT    │          │ Optimization    │
   └────────┘          │ (Automatik-LLM) │
                       └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ Web Search      │
                       │ (Multi-API)     │
                       │ → ~30 URLs      │
                       └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ URL-Ranking     │
                       │ (Automatik-LLM) │
                       │ → Top 3/7 URLs  │
                       └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ PARALLEL TASKS  │
                       ├─────────────────┤
                       │ • Scraping      │
                       │   (ranked URLs) │
                       │ • LLM Preload   │
                       │   (async)       │
                       └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ Context Build   │
                       └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ Main LLM        │
                       │ (streaming)     │
                       └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ Cache-Speicher  │
                       │ (TTL vom LLM)   │
                       └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │ RESPONSE        │
                       └─────────────────┘
```

### 📁 Code-Struktur-Referenz

**Kern-Einstiegspunkte:**
- `aifred/state.py` - Haupt-State-Management, send_message()

**Automatik-Modus:**
- `aifred/lib/conversation_handler.py` - Entscheidungslogik, RAG-Kontext

**Web-Research-Pipeline:**
- `aifred/lib/research/orchestrator.py` - Top-Level-Orchestrierung (inkl. URL-Ranking)
- `aifred/lib/research/cache_handler.py` - Session-Cache
- `aifred/lib/research/query_processor.py` - Query-Optimierung + Suche
- `aifred/lib/research/url_ranker.py` - LLM-basiertes URL-Relevanz-Ranking (NEU)
- `aifred/lib/research/scraper_orchestrator.py` - Paralleles Scraping
- `aifred/lib/research/context_builder.py` - Context-Building + LLM

**Unterstützende Module:**
- `aifred/lib/vector_cache.py` - ChromaDB semantischer Cache
- `aifred/lib/rag_context_builder.py` - RAG-Kontext aus Cache
- `aifred/lib/intent_detector.py` - Temperatur-Auswahl
- `aifred/lib/agent_tools.py` - Web-Suche, Scraping, Context-Building

### 📝 Automatik-LLM Prompts Referenz

Das Automatik-LLM nutzt dedizierte Prompts in `prompts/{de,en}/automatik/` für verschiedene Entscheidungen:

| Prompt | Sprache | Wann aufgerufen | Zweck |
|--------|---------|-----------------|-------|
| `intent_detection.txt` | nur EN | Pre-Processing | Query-Intent bestimmen (FACTUAL/MIXED/CREATIVE) und Addressee |
| `research_decision.txt` | DE + EN | Phase 4 | Entscheiden ob Web-Recherche nötig + Queries generieren |
| `rag_relevance_check.txt` | DE + EN | Phase 2 (RAG) | Prüfen ob Cache-Eintrag zur aktuellen Frage relevant ist |
| `followup_intent_detection.txt` | DE + EN | Cache-Nachfrage | Erkennen ob User mehr Details aus Cache möchte |
| `url_ranking.txt` | nur EN | Phase 2.5 | URLs nach Relevanz ranken (Output: numerische Indizes) |

**Sprach-Regeln:**
- **nur EN**: Output ist strukturiert/numerisch (parsebar), Sprache beeinflusst Ergebnis nicht
- **DE + EN**: Output hängt von User-Sprache ab oder erfordert semantisches Verständnis in dieser Sprache

**Prompt-Verzeichnisstruktur:**
```
prompts/
├── de/
│   └── automatik/
│       ├── research_decision.txt      # Deutsche Queries für deutsche User
│       ├── rag_relevance_check.txt    # Deutsches semantisches Matching
│       └── followup_intent_detection.txt
└── en/
    └── automatik/
        ├── intent_detection.txt       # Universelle Intent-Erkennung
        ├── research_decision.txt      # Englische Queries (Query 1 immer EN)
        ├── rag_relevance_check.txt    # Englisches semantisches Matching
        ├── followup_intent_detection.txt
        └── url_ranking.txt            # Numerischer Output (Indizes)
```

---

## 🌐 REST API (Fernsteuerung)

AIfred bietet eine vollständige REST-API für programmatische Steuerung - ermöglicht Fernbedienung via Cloud, Automatisierungs-Systeme und Drittanbieter-Integrationen.

### Hauptmerkmale

- **Vollständige Fernsteuerung**: AIfred von überall via HTTPS steuern
- **Live Browser-Sync**: API-Änderungen erscheinen automatisch im Browser (kein Refresh nötig)
- **Session-Management**: Zugriff und Verwaltung mehrerer Browser-Sessions
- **OpenAPI Dokumentation**: Interaktive Swagger UI unter `/docs`

### API Endpoints

Die API ermöglicht **reine Fernsteuerung** - Messages werden in Browser-Sessions injiziert, der Browser führt die vollständige Verarbeitung durch (Intent Detection, Multi-Agent, Research, etc.). So sieht der User alles live im Browser.

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/health` | GET | Health-Check mit Backend-Status |
| `/api/settings` | GET | Alle Einstellungen abrufen |
| `/api/settings` | PATCH | Einstellungen ändern (partielles Update) |
| `/api/models` | GET | Verfügbare Modelle auflisten |
| `/api/chat/inject` | POST | Nachricht in Browser-Session injizieren |
| `/api/chat/status` | GET | Inferenz-Status abfragen (is_generating, message_count) |
| `/api/chat/history` | GET | Chat-Verlauf abrufen |
| `/api/chat/clear` | POST | Chat-Verlauf löschen |
| `/api/sessions` | GET | Alle Browser-Sessions auflisten |
| `/api/system/restart-ollama` | POST | Ollama neustarten |
| `/api/system/restart-aifred` | POST | AIfred neustarten |
| `/api/calibrate` | POST | Kontext-Kalibrierung starten |

### Browser-Synchronisation

Wenn du Einstellungen änderst oder Nachrichten via API sendest, aktualisiert sich das Browser-UI automatisch:

- **Chat-Sync**: Via API gesendete Nachrichten erscheinen im Browser innerhalb von 2 Sekunden
- **Settings-Sync**: Model-Änderungen, RoPE-Faktoren, Temperatur etc. werden live im UI aktualisiert

Dies ermöglicht echte Fernsteuerung - ändere AIfred's Konfiguration von einem anderen Gerät und sieh die Änderungen sofort in jedem verbundenen Browser.

### Beispiel-Verwendung

```bash
# Aktuelle Einstellungen abrufen
curl http://localhost:8002/api/settings

# Model und RoPE-Faktor ändern
curl -X PATCH http://localhost:8002/api/settings \
  -H "Content-Type: application/json" \
  -d '{"aifred_model": "qwen3:14b", "sokrates_rope_factor": 2.0}'

# Nachricht injizieren (Browser verarbeitet und zeigt live)
curl -X POST http://localhost:8002/api/chat/inject \
  -H "Content-Type: application/json" \
  -d '{"message": "Was ist Python?", "device_id": "abc123..."}'

# Inferenz-Status abfragen
curl "http://localhost:8002/api/chat/status?device_id=abc123..."

# Alle Browser-Sessions auflisten
curl http://localhost:8002/api/sessions
```

### Anwendungsfälle

- **Cloud-Steuerung**: AIfred von überall via HTTPS/API bedienen
- **Home-Automation**: Integration mit Home Assistant, Node-RED, etc.
- **Sprachassistenten**: Alexa/Google Home können AIfred-Anfragen senden
- **Batch-Verarbeitung**: Automatisierte Abfragen via Scripts
- **Mobile Apps**: Custom-Apps können die API nutzen

---

## 🚀 Installation

### Voraussetzungen
- Python 3.10+
- **LLM Backend** (wähle eins):
  - **llama.cpp** via llama-swap (GGUF-Modelle) - beste Performance, volle GPU-Kontrolle ([Setup-Anleitung](docs/en/guides/llamacpp-setup.md))
  - **Ollama** (einfach, GGUF-Modelle) - empfohlen für Einsteiger
  - **vLLM** (schnell, AWQ-Modelle) - beste Performance für AWQ (erfordert Compute Capability 7.5+)
  - **TabbyAPI** (ExLlamaV2/V3, EXL2-Modelle) - experimentell

> **Zero-Config Modell-Management (llama.cpp-Backend):** Nach dem einmaligen Setup genügt `ollama pull model` oder `hf download ...`, dann llama-swap neu starten — der Autoscan konfiguriert alles automatisch (YAML-Einträge, Gruppen, VRAM-Cache). Vollständige Anleitung: [docs/en/guides/deployment.md](docs/en/guides/deployment.md).
- 8GB+ RAM (12GB+ empfohlen für größere Modelle)
- Docker (für ChromaDB Vector Cache)
- **GPU**: NVIDIA GPU empfohlen (siehe [GPU Compatibility Guide](docs/GPU_COMPATIBILITY.md))

### Setup

1. **Repository klonen**:
```bash
git clone https://github.com/yourusername/AIfred-Intelligence.git
cd AIfred-Intelligence
```

2. **Virtual Environment erstellen**:
```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# oder
venv\Scripts\activate     # Windows
```

3. **Dependencies installieren**:
```bash
pip install -r requirements.txt
# Playwright Browser installieren (für JS-heavy Seiten)
playwright install chromium
```

**Haupt-Dependencies** (siehe `requirements.txt`):
| Kategorie | Packages |
|-----------|----------|
| Framework | reflex, fastapi, pydantic |
| LLM Backends | httpx, openai, pynvml, psutil |
| Web Research | trafilatura, playwright, requests, pymupdf |
| Vector Cache | chromadb, ollama, numpy |
| Audio (STT/TTS) | edge-tts, XTTS v2 (Docker), openai-whisper |

4. **Umgebungsvariablen** (.env):
```env
# API Keys für Web-Recherche
BRAVE_API_KEY=your_key_here
TAVILY_API_KEY=your_key_here

# Ollama Konfiguration
OLLAMA_BASE_URL=http://localhost:11434
```

5. **LLM Models installieren**:

**Option A: Alle Models (Empfohlen)**
```bash
# Master-Script für beide Backends
./scripts/download_all_models.sh
```

**Option B: Nur Ollama (GGUF) - Einfachste Installation**
```bash
# Ollama Models (GGUF Q4/Q8)
./scripts/download_ollama_models.sh

# Empfohlene Core-Modelle:
# - qwen3:30b-instruct (18GB) - Haupt-LLM, 256K context
# - qwen3:8b (5.2GB) - Automatik, optional thinking
# - qwen2.5:3b (1.9GB) - Ultra-schnelle Automatik
```

**Option C: Nur vLLM (AWQ) - Beste Performance**
```bash
# vLLM installieren (falls noch nicht geschehen)
pip install vllm

# vLLM Models (AWQ Quantization)
./scripts/download_vllm_models.sh

# Empfohlene Modelle:
# - Qwen3-8B-AWQ (~5GB, 40K→128K mit YaRN)
# - Qwen3-14B-AWQ (~8GB, 32K→128K mit YaRN)
# - Qwen2.5-14B-Instruct-AWQ (~8GB, 128K native)

# vLLM Server starten mit YaRN (64K context)
./venv/bin/vllm serve Qwen/Qwen3-14B-AWQ \
  --quantization awq_marlin \
  --port 8001 \
  --rope-scaling '{"rope_type":"yarn","factor":2.0,"original_max_position_embeddings":32768}' \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.85

# Systemd Service einrichten: siehe docs/infrastructure/
```

**Option D: TabbyAPI (EXL2) - Experimentell**
```bash
# Noch nicht vollständig implementiert
# Siehe: https://github.com/theroyallab/tabbyAPI
```

6. **ChromaDB Vector Cache starten** (Docker):
```bash
cd docker
docker compose up -d chromadb
cd ..
```

**Optional: SearXNG auch starten** (lokale Suchmaschine):
```bash
cd docker
docker compose --profile full up -d
cd ..
```

**ChromaDB Cache zurücksetzen** (bei Bedarf):

*Option 1: Kompletter Neustart (löscht alle Daten)*
```bash
cd docker
docker compose stop chromadb
cd ..
rm -rf docker/aifred_vector_cache/
cd docker
docker compose up -d chromadb
cd ..
```

*Option 2: Nur Collection löschen (während Container läuft)*
```bash
./venv/bin/python -c "
import chromadb
from chromadb.config import Settings

client = chromadb.HttpClient(
    host='localhost',
    port=8000,
    settings=Settings(anonymized_telemetry=False)
)

try:
    client.delete_collection('research_cache')
    print('✅ Collection gelöscht')
except Exception as e:
    print(f'⚠️ Fehler: {e}')
"
```

7. **XTTS Voice Cloning starten** (Optional, Docker):

XTTS v2 bietet hochwertige Stimmklonung mit mehrsprachiger Unterstützung und intelligenter GPU/CPU-Auswahl.

```bash
cd docker/xtts
docker compose up -d
```

Erster Start dauert ~2-3 Minuten (Modell-Download ~1.5GB). Danach ist XTTS als TTS-Engine in den UI-Einstellungen verfügbar.

**Features:**
- 58 eingebaute Stimmen + eigene Stimmklonung (6-10s Referenz-Audio)
- Automatische GPU/CPU-Auswahl basierend auf verfügbarem VRAM
- **Manueller CPU-Mode Toggle**: GPU-VRAM für größeres LLM-Kontextfenster sparen (langsamere TTS)
- Mehrsprachige Unterstützung (16 Sprachen) mit automatischem Code-Switching (DE/EN gemischt)
- Agentenspezifische Stimmen mit individueller Tonhöhe und Geschwindigkeit
- **Multi-Agent TTS Queue**: Sequentielle Wiedergabe von AIfred → Sokrates → Salomo
- Asynchrone TTS-Generierung (blockiert nächste LLM-Inferenz nicht)
- **VRAM-Management**: Bei GPU-Mode werden ~2 GB VRAM reserviert und vom LLM-Kontextfenster abgezogen

Siehe [docker/xtts/README.md](docker/xtts/README.md) für vollständige Dokumentation.

8. **MOSS-TTS Voice Cloning starten** (Optional, Docker):

MOSS-TTS (MossTTSLocal 1.7B) bietet State-of-the-Art Zero-Shot Voice Cloning in 20 Sprachen mit hervorragender Sprachqualität.

```bash
cd docker/moss-tts
docker compose up -d
```

Erster Start dauert ~5-10 Minuten (Modell-Download ~3-5 GB). Danach ist MOSS-TTS als TTS-Engine in den UI-Einstellungen verfügbar.

**Features:**
- Zero-Shot Voice Cloning (Referenz-Audio, keine Transkription nötig)
- 20 Sprachen inkl. Deutsch und Englisch
- Hervorragende Sprachqualität (EN SIM 73.42%, ZH SIM 78.82% - beste Open-Source)

**Einschränkungen:**
- **Hoher VRAM-Verbrauch**: ~11,5 GB in BF16 (vs. 2 GB bei XTTS)
- **Nicht für Streaming geeignet**: ~18-22s pro Satz (vs. ~1-2s bei XTTS)
- **VRAM-Management**: Bei GPU-Mode werden ~11,5 GB VRAM reserviert und vom LLM-Kontextfenster abgezogen
- Empfohlen für hochqualitative Offline-Audiogenerierung, nicht für Echtzeit-Streaming

9. **Starten**:
```bash
reflex run
```

Die App läuft dann unter: http://localhost:3002

---

## ⚙️ Backend-Wechsel & Settings

### Multi-Backend Support

AIfred unterstützt verschiedene LLM-Backends, die in der UI dynamisch gewechselt werden können:

- **llama.cpp** (via llama-swap): GGUF-Modelle, beste Roh-Performance (+43% Generation, +30% Prompt-Processing vs Ollama), volle GPU-Kontrolle, Multi-GPU-Unterstützung. Verwendet eine 3-stufige Architektur: **llama-swap** (Go-Proxy, Modell-Management) → **llama-server** (Inferenz) → **llama.cpp** (Library). Automatische VRAM-Kalibrierung via 3-phasiger Binärer Suche: GPU-only Kontext-Sizing → Speed-Variante mit optimierter Tensor-Split für maximalen Multi-GPU-Durchsatz → Hybrid NGL-Fallback für übergroße Modelle. Siehe [Setup-Anleitung](docs/llamacpp-setup.md).
- **Ollama**: GGUF-Modelle (Q4/Q8), einfachste Installation, automatisches Modell-Management, gute Performance nach v2.32.0-Optimierungen
- **vLLM**: AWQ-Modelle (4-bit), beste Performance mit AWQ Marlin Kernel
- **TabbyAPI**: EXL2-Modelle (ExLlamaV2/V3) - experimentell, nur Basis-Unterstützung

### GPU Compatibility Detection

AIfred erkennt automatisch beim Start deine GPU und warnt vor inkompatiblen Backend-Konfigurationen:

- **Tesla P40 / GTX 10 Series** (Pascal): Nutze llama.cpp oder Ollama (GGUF) - vLLM/AWQ wird nicht unterstützt
- **RTX 20+ Series** (Turing/Ampere/Ada): llama.cpp (GGUF) oder vLLM (AWQ) empfohlen für beste Performance

Detaillierte Informationen: [GPU_COMPATIBILITY.md](docs/GPU_COMPATIBILITY.md)

### Settings-Persistenz

Settings werden in `data/settings.json` gespeichert:

**Per-Backend Modell-Speicherung:**
- Jedes Backend merkt sich seine zuletzt verwendeten Modelle
- Beim Backend-Wechsel werden automatisch die richtigen Modelle wiederhergestellt
- Beim ersten Start werden Defaults aus `aifred/lib/config.py` verwendet

**Sampling-Parameter-Persistenz:**

| Parameter | Gespeichert? | Bei Neustart | Bei Modellwechsel |
|-----------|-------------|--------------|-------------------|
| Temperature | Ja (settings.json) | Beibehalten | Reset auf YAML |
| Top-K, Top-P, Min-P, Repeat-Penalty | Nein | Reset auf YAML | Reset auf YAML |

Quelle der Sampling-Defaults: `--temp`, `--top-k`, `--top-p`, `--min-p`, `--repeat-penalty` Flags in der llama-swap YAML-Config (`~/.config/llama-swap/config.yaml`).

**Beispiel Settings-Struktur:**
```json
{
  "backend_type": "vllm",
  "enable_thinking": true,
  "backend_models": {
    "ollama": {
      "selected_model": "qwen3:8b",
      "automatik_model": "qwen2.5:3b"
    },
    "vllm": {
      "selected_model": "Qwen/Qwen3-8B-AWQ",
      "automatik_model": "Qwen/Qwen3-4B-AWQ"
    }
  }
}
```

### Reasoning Mode (Chain-of-Thought)

AIfred unterstützt per-Agent Reasoning-Konfiguration für verbesserte Antwortqualität.

**Per-Agent Reasoning Toggles** (v2.23.0):

Jeder Agent (AIfred, Sokrates, Salomo) hat seinen eigenen Reasoning-Toggle in den LLM-Einstellungen. Diese Toggles steuern **beide** Mechanismen:

1. **Reasoning Prompt**: Chain-of-Thought Anweisungen im System-Prompt (funktioniert für ALLE Modelle)
2. **enable_thinking Flag**: Technisches Flag für Thinking-Modelle (Qwen3, QwQ, NemoTron)

| Toggle | Reasoning Prompt | enable_thinking | Effekt |
|--------|------------------|-----------------|--------|
| **ON** | ✅ Injiziert | ✅ True | Voller CoT mit `<think>`-Blocks (Thinking-Modelle) |
| **ON** | ✅ Injiziert | ✅ True | CoT-Anweisungen befolgt (Instruct-Modelle, kein `<think>`) |
| **OFF** | ❌ Nicht injiziert | ❌ False | Direkte Antworten, kein Reasoning |

**Design-Begründung:**
- Instruct-Modelle (ohne native `<think>`-Tags) profitieren von CoT-Prompt-Anweisungen
- Thinking-Modelle erhalten beides: CoT-Prompt + technisches Flag für `<think>`-Block-Generierung
- Dieser einheitliche Ansatz ermöglicht konsistentes Verhalten unabhängig vom Modelltyp

**Weitere Features:**
- **Formatierung**: Denkprozess als ausklappbares Collapsible mit Modellname und Inferenzzeit
- **Temperature**: Unabhängig vom Reasoning - nutzt Intent Detection (auto) oder manuellen Wert in der Sampling-Tabelle
- **Automatik-LLM**: Reasoning immer DEAKTIVIERT für Automatik-Entscheidungen (8x schneller)

---

## 🏗️ Architektur

### Directory Structure
```
AIfred-Intelligence/
├── aifred/
│   ├── backends/          # LLM Backend Adapters
│   │   ├── base.py           # Abstract Base Class
│   │   ├── llamacpp.py       # llama.cpp Backend (GGUF via llama-swap)
│   │   ├── ollama.py         # Ollama Backend (GGUF)
│   │   ├── vllm.py           # vLLM Backend (AWQ)
│   │   └── tabbyapi.py       # TabbyAPI Backend (EXL2)
│   ├── lib/               # Core Libraries
│   │   ├── multi_agent.py       # Multi-Agent System (AIfred, Sokrates, Salomo)
│   │   ├── context_manager.py   # History-Kompression
│   │   ├── conversation_handler.py # Automatik-Modus, RAG-Kontext
│   │   ├── config.py            # Default Settings
│   │   ├── vector_cache.py      # ChromaDB Vector Cache
│   │   ├── model_vram_cache.py  # Unified VRAM Cache (alle Backends)
│   │   ├── llamacpp_calibration.py # llama.cpp Binary Search Kalibrierung
│   │   ├── gguf_utils.py        # GGUF-Metadaten-Reader (nativer Kontext, Quant)
│   │   ├── research/            # Web-Research Module
│   │   │   ├── orchestrator.py      # Research Orchestrierung
│   │   │   ├── url_ranker.py        # LLM-basiertes URL-Ranking
│   │   │   └── query_processor.py   # Query Processing
│   │   └── tools/               # Tool-Implementierungen
│   │       ├── search_tools.py      # Parallele Websuche
│   │       └── scraper_tool.py      # Paralleles Web-Scraping
│   ├── aifred.py          # Hauptanwendung / UI
│   └── state.py           # Reflex State Management
├── prompts/               # System Prompts (de/en)
├── scripts/               # Utility Scripts
├── docs/                  # Dokumentation
│   ├── infrastructure/          # Service-Setup Anleitungen
│   ├── architecture/            # Architektur-Docs
│   └── GPU_COMPATIBILITY.md     # GPU-Kompatibilitätsmatrix
├── data/                  # Laufzeitdaten (Settings, Sessions, Caches)
│   ├── settings.json            # Benutzereinstellungen
│   ├── model_vram_cache.json    # VRAM-Kalibrierungsdaten (alle Backends)
│   ├── sessions/                # Chat-Sessions
│   └── logs/                    # Debug-Logs
├── docker/                # Docker-Konfigurationen
│   └── aifred_vector_cache/     # ChromaDB Docker Setup
└── CHANGELOG.md           # Projekt-Changelog
```

### History Compression System

Bei 70% Context-Auslastung werden automatisch ältere Konversationen komprimiert mit **PRE-MESSAGE Checks** (v2.12.0):

| Parameter | Wert | Beschreibung |
|-----------|------|--------------|
| `HISTORY_COMPRESSION_TRIGGER` | 0.7 (70%) | Bei dieser Context-Auslastung wird komprimiert |
| `HISTORY_COMPRESSION_TARGET` | 0.3 (30%) | Ziel nach Kompression (Platz für ~2 Roundtrips) |
| `HISTORY_SUMMARY_RATIO` | 0.25 (4:1) | Summary = 25% des zu komprimierenden Inhalts |
| `HISTORY_SUMMARY_MIN_TOKENS` | 500 | Minimum für sinnvolle Zusammenfassungen |
| `HISTORY_SUMMARY_TOLERANCE` | 0.5 (50%) | Erlaubte Überschreitung, darüber wird gekürzt |
| `HISTORY_SUMMARY_MAX_RATIO` | 0.2 (20%) | Max Context-Anteil für Summaries (NEU) |

**Ablauf (PRE-MESSAGE):**
1. **PRE-CHECK** vor jedem LLM-Aufruf (nicht danach!)
2. **Trigger** bei 70% Context-Auslastung
3. **Dynamisches max_summaries** basierend auf Context-Größe (20% Budget / 500 tok)
4. **FIFO cleanup**: Falls zu viele Summaries, älteste wird zuerst gelöscht
5. **Sammle** älteste Messages bis remaining < 30%
6. **Komprimiere** gesammelte Messages zu Summary (4:1 Ratio)
7. **Neue History** = [Summaries] + [verbleibende Messages]

**Dynamische Summary-Limits:**
| Context | Max Summaries | Berechnung |
|---------|---------------|------------|
| 4K | 1-2 | 4096 × 0.2 / 500 = 1,6 |
| 8K | 3 | 8192 × 0.2 / 500 = 3,3 |
| 32K | 10 | 32768 × 0.2 / 500 = 13 → gedeckelt bei 10 |

**Token-Estimation:** Ignoriert `<details>`, `<span>`, `<think>` Tags (gehen nicht ans LLM)

**Beispiele nach Context-Größe:**
| Context | Trigger | Ziel | Komprimiert | Summary |
|---------|---------|------|-------------|---------|
| 7K | 4.900 tok | 2.100 tok | ~2.800 tok | ~700 tok |
| 40K | 28.000 tok | 12.000 tok | ~16.000 tok | ~4.000 tok |
| 200K | 140.000 tok | 60.000 tok | ~80.000 tok | ~20.000 tok |

**Inline Summaries (UI, v2.14.2+):**
- Summaries erscheinen inline wo die Kompression stattfand
- Jede Summary als Collapsible mit Header (Nummer, Message-Count)
- FIFO gilt nur für `llm_history` (LLM sieht 1 Summary)
- `chat_history` behält ALLE Summaries (User sieht vollständige History)

### Vector Cache & RAG System

AIfred nutzt ein mehrstufiges Cache-System basierend auf **semantischer Ähnlichkeit** (Cosine Distance) mit rein semantischer Deduplizierung und intelligenter Cache-Nutzung bei expliziten Recherche-Keywords.

#### Cache-Entscheidungs-Logik

**Phase 0: Explizite Recherche-Keywords**
```
User Query: "recherchiere Python" / "google Python" / "suche im internet Python"
└─ Explizites Keyword erkannt → Cache-Check ZUERST
   ├─ Distance < 0.05 (praktisch identisch)
   │  └─ ✅ Cache-Hit (0.15s statt 100s) - Zeigt Alter transparent an
   └─ Distance ≥ 0.05 (nicht identisch)
      └─ Neue Web-Recherche (User will neue Daten)
```

**Phase 1a: Direct Cache Hit Check**
```
User Query → ChromaDB Similarity Search
├─ Distance < 0.5 (HIGH Confidence)
│  └─ ✅ Use Cached Answer (sofort, keine Zeit-Checks mehr!)
├─ Distance 0.5-1.2 (MEDIUM Confidence) → Continue to Phase 1b (RAG)
└─ Distance > 1.2 (LOW Confidence) → Continue to Phase 2 (Research Decision)
```

**Phase 1b: RAG Context Check**
```
Cache Miss (d ≥ 0.5) → Query for RAG Candidates (0.5 ≤ d < 1.2)
├─ Found RAG Candidates?
│  ├─ YES → Automatik-LLM checks relevance for each candidate
│  │   ├─ Relevant (semantic match) → Inject as System Message Context
│  │   │   Example: "Python" → "FastAPI" ✅ (FastAPI is Python framework)
│  │   └─ Not Relevant → Skip
│  │       Example: "Python" → "Weather" ❌ (no connection)
│  └─ NO → Continue to Phase 2
└─ LLM Answer with RAG Context (Source: "Cache+LLM (RAG)")
```

**Phase 2: Research Decision**
```
No Direct Cache Hit & No RAG Context
└─ Automatik-LLM decides: Web Research needed?
   ├─ YES → Web Research + Cache Result
   └─ NO  → Pure LLM Answer (Source: "LLM-Trainingsdaten")
```

#### Semantic Deduplication

**Beim Speichern in Vector Cache:**
```
New Research Result → Check for Semantic Duplicates
└─ Distance < 0.3 (semantisch ähnlich)
   └─ ✅ IMMER Update
      - Löscht alten Eintrag
      - Speichert neuen Eintrag
      - Garantiert: Neueste Daten werden verwendet
```

Rein semantische Deduplizierung ohne Zeit-Checks → Konsistentes Verhalten.

#### Cache Distance Thresholds

| Distance | Confidence | Behavior | Example |
|----------|-----------|----------|---------|
| `0.0 - 0.05` | EXACT | Explizite Recherche nutzt Cache | Identische Query |
| `0.05 - 0.5` | HIGH | Direct cache hit | "Python tutorial" vs "Python Anleitung" |
| `0.5 - 1.2` | MEDIUM | RAG candidate (relevance check via LLM) | "Python" vs "FastAPI" |
| `1.2+` | LOW | Cache miss → Research decision | "Python" vs "Weather" |

#### ChromaDB Maintenance Tool

Wartungstool für Vector Cache:
```bash
# Stats anzeigen
python3 chroma_maintenance.py --stats

# Duplikate finden
python3 chroma_maintenance.py --find-duplicates

# Duplikate entfernen (Dry-Run)
python3 chroma_maintenance.py --remove-duplicates

# Duplikate entfernen (Execute)
python3 chroma_maintenance.py --remove-duplicates --execute

# Alte Einträge löschen (> 30 Tage)
python3 chroma_maintenance.py --remove-old 30 --execute
```

#### RAG (Retrieval-Augmented Generation) Mode

**How it works**:
1. Query finds related cache entries (distance 0.5-1.2)
2. Automatik-LLM checks if cached content is relevant to current question
3. Relevant entries are injected as system message: "Previous research shows..."
4. Main LLM combines cached context + training knowledge for enhanced answer

**Example Flow**:
```
User: "Was ist Python?" → Web Research → Cache Entry 1 (d=0.0)
User: "Was ist FastAPI?" → RAG finds Entry 1 (d=0.7)
  → LLM checks: "Python" relevant for "FastAPI"? YES (FastAPI uses Python)
  → Inject Entry 1 as context → Enhanced LLM answer
  → Source: "Cache+LLM (RAG)"
```

**Benefits**:
- Leverages related past research without exact cache hits
- Avoids false context (LLM filters irrelevant entries)
- Multi-level context awareness (cache + conversation history)

#### TTL-Based Cache System (Volatility)

Das Main LLM bestimmt die Cache-Lebensdauer via `<volatility>` Tag in der Antwort:

| Volatility | TTL | Anwendungsfall |
|------------|-----|----------------|
| `DAILY` | 24h | News, aktuelle Ereignisse, "neueste Entwicklungen" |
| `WEEKLY` | 7 Tage | Politische Updates, semi-aktuelle Themen |
| `MONTHLY` | 30 Tage | Statistiken, Reports, weniger volatile Daten |
| `PERMANENT` | ∞ | Zeitlose Fakten ("Was ist Python?") |

**Automatisches Cleanup**: Hintergrund-Task läuft alle 12 Stunden, löscht abgelaufene Einträge.

#### Configuration

Cache-Verhalten in `aifred/lib/config.py`:

```python
# Cache Distance Thresholds
CACHE_DISTANCE_HIGH = 0.5        # < 0.5 = HIGH confidence cache hit
CACHE_DISTANCE_DUPLICATE = 0.3   # < 0.3 = semantic duplicate (wird immer gemerged)
CACHE_DISTANCE_RAG = 1.2         # < 1.2 = ähnlich genug für RAG-Kontext

# TTL (Time-To-Live)
TTL_HOURS = {
    'DAILY': 24,
    'WEEKLY': 168,
    'MONTHLY': 720,
    'PERMANENT': None
}
```

**RAG Relevance Check**: Nutzt Automatik-LLM mit dediziertem Prompt (`prompts/de/rag_relevance_check.txt`)

---

## 🔧 Konfiguration

Alle wichtigen Parameter in `aifred/lib/config.py`:

```python
# History Compression (dynamisch, prozentual)
HISTORY_COMPRESSION_TRIGGER = 0.7    # 70% - Wann komprimieren?
HISTORY_COMPRESSION_TARGET = 0.3     # 30% - Wohin komprimieren?
HISTORY_SUMMARY_RATIO = 0.25         # 25% = 4:1 Kompression
HISTORY_SUMMARY_MIN_TOKENS = 500     # Minimum für Summaries
HISTORY_SUMMARY_TOLERANCE = 0.5      # 50% Überschreitung erlaubt

# Intent-basierte Temperatur
INTENT_TEMPERATURE_FAKTISCH = 0.2    # Faktische Anfragen
INTENT_TEMPERATURE_GEMISCHT = 0.5    # Gemischte Anfragen
INTENT_TEMPERATURE_KREATIV = 1.0     # Kreative Anfragen

# Backend-spezifische Default Models (in BACKEND_DEFAULT_MODELS)
# Ollama: qwen3:4b-instruct-2507-q4_K_M (Automatik), qwen3-vl:8b (Vision)
# vLLM: cpatonn/Qwen3-4B-Instruct-2507-AWQ-4bit, etc.
```

### HTTP Timeout Konfiguration

In `aifred/backends/ollama.py`:
- **HTTP Client Timeout**: 300 Sekunden (5 Minuten)
- Erhöht von 60s für große Research-Anfragen mit 30KB+ Context
- Verhindert Timeout-Fehler bei erster Token-Generation

### Restart-Button Verhalten

Der AIfred Restart-Button startet den systemd-Service neu:
- Führt `systemctl restart aifred-intelligence` aus
- Browser lädt automatisch nach kurzer Verzögerung neu
- Debug-Logs werden geleert, Sessions bleiben erhalten

---

## 📦 Deployment

### Systemd Service

Für produktiven Betrieb als Service sind vorkonfigurierte Service-Dateien im `systemd/` Verzeichnis verfügbar.

**⚠️ WICHTIG**: Die Umgebungsvariable `AIFRED_ENV=prod` **MUSS** gesetzt sein, damit AIfred auf dem MiniPC läuft und nicht auf den Entwicklungsrechner weiterleitet!

#### Schnellinstallation

```bash
# 1. Service-Dateien kopieren
sudo cp systemd/aifred-chromadb.service /etc/systemd/system/
sudo cp systemd/aifred-intelligence.service /etc/systemd/system/

# 2. Services aktivieren und starten
sudo systemctl daemon-reload
sudo systemctl enable aifred-chromadb.service aifred-intelligence.service
sudo systemctl start aifred-chromadb.service aifred-intelligence.service

# 3. Status prüfen
systemctl status aifred-chromadb.service
systemctl status aifred-intelligence.service
```

Siehe [systemd/README.md](systemd/README.md) für Details, Troubleshooting und Monitoring.

#### Discord-Kanal einrichten

1. [Discord Developer Portal](https://discord.com/developers/applications) → "New Application"
2. **Bot**-Seite: "Reset Token" → Token kopieren. **Message Content Intent** einschalten
3. **Public Bot** ausschalten (nur du solltest den Bot hinzufügen können)
4. **OAuth2**-Seite → URL-Generator: Scope `bot` auswählen, Berechtigungen: "Nachrichten senden", "Nachrichtenverlauf anzeigen", "Kanäle ansehen"
5. Generierte URL im Browser öffnen → Server auswählen → Autorisieren
6. Privaten Kanal auf dem Server erstellen (z.B. `#aifred`), Bot hinzufügen
7. Rechtsklick auf den Kanal → "Kanal-ID kopieren" (Entwicklermodus: Discord Einstellungen → Erweitert → Entwicklermodus)
8. In AIfred: Plugin Manager → Discord → Zahnrad → Bot-Token + Channel-ID eintragen → Speichern & Aktivieren

#### Service-Dateien (Referenz)

**1. ChromaDB Service** (`systemd/aifred-chromadb.service`):
```ini
[Unit]
Description=AIfred ChromaDB Vector Cache (Docker)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/mp/Projekte/AIfred-Intelligence/docker
ExecStart=/usr/bin/docker compose up -d chromadb
ExecStop=/usr/bin/docker compose stop chromadb
```

**2. AIfred Intelligence Service** (`systemd/aifred-intelligence.service`):
```ini
[Unit]
Description=AIfred Intelligence Voice Assistant (Reflex Version)
After=network.target ollama.service aifred-chromadb.service
Wants=ollama.service
Requires=aifred-chromadb.service

[Service]
Type=simple
User=__USER__
Group=__USER__
WorkingDirectory=__PROJECT_DIR__
Environment="PATH=__PROJECT_DIR__/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=__PROJECT_DIR__/venv/bin/python -m reflex run --env prod --frontend-port 3002 --backend-port 8002 --backend-host 0.0.0.0
Restart=always
KillMode=control-group
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**⚠️ Wichtig: Ersetze die Platzhalter** `__USER__` und `__PROJECT_DIR__` mit deinen tatsächlichen Werten!

#### Umgebungskonfiguration (.env)

Für Produktions-/Externen Zugriff erstelle eine `.env` Datei im Projektverzeichnis (diese Datei ist in .gitignore und wird NICHT ins Repository gepusht):

```bash
# Umgebungsmodus (erforderlich für Produktion)
AIFRED_ENV=prod

# Backend API URL für externen Zugriff via nginx Reverse Proxy
# Setze dies auf deine externe Domain/IP für HTTPS-Zugriff
AIFRED_API_URL=https://deine-domain.de:8443

# API Keys für Web-Recherche (optional)
BRAVE_API_KEY=dein_brave_api_key
TAVILY_API_KEY=dein_tavily_api_key

# Ollama Konfiguration
OLLAMA_BASE_URL=http://localhost:11434
# WICHTIG: Setze OLLAMA_NUM_PARALLEL=1 in der Ollama Service-Konfiguration (siehe Performance-Abschnitt unten)

# Backend-URL für statische Dateien (HTML-Preview, Bilder)
# Mit NGINX: Leer lassen oder weglassen - NGINX leitet /_upload/ ans Backend
# Ohne NGINX (Dev): Auf Backend-URL setzen für direkten Zugriff
# BACKEND_URL=http://localhost:8002
```

**Warum wird `AIFRED_API_URL` benötigt?**

Das Reflex-Frontend muss wissen, wo das Backend erreichbar ist. Ohne diese Einstellung:
- Das Frontend erkennt automatisch die lokale IP (z.B. `http://192.168.0.252:8002`)
- Das funktioniert für lokalen Netzwerkzugriff, aber scheitert bei externem HTTPS-Zugriff
- Externe Nutzer würden WebSocket-Verbindungsfehler zu `localhost` sehen

Mit `AIFRED_API_URL=https://deine-domain.de:8443`:
- Alle API/WebSocket-Verbindungen gehen über deinen nginx Reverse Proxy
- HTTPS funktioniert korrekt für externen Zugriff
- Lokaler HTTP-Zugriff funktioniert weiterhin

**Warum `--env prod`?**

Das `--env prod` Flag im ExecStart:
- Deaktiviert Vite Hot Module Replacement (HMR) WebSocket
- Verhindert "failed to connect to websocket localhost:3002" Fehler
- Reduziert Ressourcenverbrauch (kein Dev-Server Overhead)
- Kompiliert trotzdem bei Neustart wenn sich Code geändert hat

**⚠️ FOUC-Problem im Prod-Modus**

Im Produktionsmodus (`--env prod`) kann ein **FOUC (Flash of Unstyled Content)** auftreten - ein kurzer Blitz von ungestyltem Text/CSS-Klassennamen beim Seiten-Reload.

**Ursache:** React Router 7 mit `prerender: true` lädt CSS asynchron (Lazy Loading). Der generierte HTML-Code ist sofort sichtbar, aber das Emotion CSS-in-JS wird erst nachgeladen.

**Lösung: Dev-Modus verwenden**

Wenn der FOUC störend ist, kann stattdessen der Dev-Modus verwendet werden:

```bash
# In .env setzen:
AIFRED_ENV=dev

# Oder --env prod aus dem systemd Service entfernen
```

**Dev-Modus Eigenschaften:**
- ✅ Kein FOUC (CSS wird synchron geladen)
- ⚠️ Etwas höherer RAM-Verbrauch (Hot Reload Server)
- ⚠️ Mehr Console-Warnungen (React Strict Mode)
- ⚠️ Nicht-minifizierte Bundles (etwas größer)

Für einen lokalen Server im Heimnetz sind diese Nachteile vernachlässigbar.

**Zusätzlich nötig für Dev-Modus mit externem Zugriff:**

> ⚠️ **WICHTIG:** Die `.web/vite.config.js` Datei wird bei Reflex-Updates überschrieben!
> Nach Updates das Patch-Script ausführen: `./scripts/patch-vite-config.sh`

In `.web/vite.config.js` muss Folgendes konfiguriert werden:

1. **allowedHosts** - für externen Domain-Zugriff:
```javascript
server: {
  allowedHosts: ["deine-domain.de", "localhost", "127.0.0.1"],
}
```

2. **proxy** - für API und TTS SSE Streaming (nötig bei Zugriff über Frontend-Port 3002):
```javascript
server: {
  proxy: {
    '/_upload': { target: 'http://0.0.0.0:8002', changeOrigin: true },
    '/api': { target: 'http://0.0.0.0:8002', changeOrigin: true },
  },
}
```

Ohne den `/api` Proxy schlägt TTS-Streaming fehl mit "text/html instead of text/event-stream" Fehlern.

2. Service aktivieren:
```bash
sudo systemctl daemon-reload
sudo systemctl enable aifred-intelligence
sudo systemctl start aifred-intelligence
```

3. **Optional: Polkit-Regel für Restart ohne sudo**

Für den Restart-Button in der Web-UI ohne Passwort-Abfrage:

`/etc/polkit-1/rules.d/50-aifred-restart.rules`:
```javascript
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.systemd1.manage-units") &&
        (action.lookup("unit") == "aifred-intelligence.service" ||
         action.lookup("unit") == "ollama.service") &&
        (action.lookup("verb") == "restart") &&
        (subject.user == "mp")) {
        return polkit.Result.YES;
    }
});
```

---

## ⚠️ Multi-User-Fähigkeiten & Einschränkungen

AIfred ist als **Single-User-System** konzipiert, unterstützt aber 2-3 gleichzeitige Nutzer mit gewissen Einschränkungen.

### ✅ Was funktioniert (gleichzeitige Nutzer)

**Session-Isolation (Reflex Framework):**
- Jeder Browser-Tab bekommt eine eigene Session mit eindeutigem `client_token` (UUID)
- **Chat-Verlauf ist isoliert** - Nutzer sehen nicht die Konversationen der anderen
- **Streaming-Antworten funktionieren parallel** - jeder Nutzer bekommt seine eigenen Echtzeit-Updates
- **Request-Queue** - Ollama queued gleichzeitige Requests automatisch intern

**Pro-Nutzer isolierter State:**
- ✅ Chat-Verlauf (`chat_history`, `llm_history`)
- ✅ Aktuelle Nachrichten und Streaming-Antworten
- ✅ Bild-Uploads und Crop-State
- ✅ Session-ID und Device-ID (Cookie-basiert)
- ✅ Failed Sources und Debug-Messages

### ⚠️ Was geteilt wird (globaler State)

**Backend-Konfiguration (geteilt zwischen allen Nutzern):**
- ⚠️ **Ausgewähltes Backend** (Ollama, vLLM, TabbyAPI, Cloud API)
- ⚠️ **Backend-URL**
- ⚠️ **Ausgewählte Modelle** (AIfred-LLM, Automatik-LLM, Sokrates-LLM, Salomo-LLM, Vision-LLM)
- ⚠️ **Verfügbare Modelle-Liste**
- ⚠️ **GPU-Info und VRAM-Cache**
- ⚠️ **vLLM-Prozess-Manager**

**Settings-Datei (`data/settings.json`):**
- ⚠️ Alle Einstellungen sind global (Temperature, Multi-Agent-Modus, RoPE-Faktoren, etc.)
- ⚠️ Wenn User A eine Einstellung ändert → sieht User B die Änderung sofort
- ⚠️ Keine nutzer-spezifischen Einstellungs-Profile

### 🎯 Praktische Nutzungs-Szenarien

**✅ SICHER: Mehrere Nutzer senden Requests**
```
Timeline (Ollama queued Requests automatisch):
─────────────────────────────────────────────────────
User A: Sendet Frage → Ollama bearbeitet → Antwort an User A
User B:               → Sendet Frage → Wartet in Queue → Ollama bearbeitet → Antwort an User B
User C:                               → Sendet Frage → Wartet in Queue → Ollama bearbeitet → Antwort an User C
```

- Jeder Nutzer bekommt seine eigene korrekte Antwort
- Ollamas interne Queue handhabt gleichzeitige Requests sequenziell
- Keine Race Conditions, solange niemand während Requests die Settings ändert

**⚠️ PROBLEMATISCH: Settings ändern während aktive Requests laufen**
```
User A: Sendet Request mit Qwen3:8b → Wird bearbeitet...
User B: Wechselt Modell zu Llama3:70b → Globaler State ändert sich!
User A: Request läuft weiter mit Qwen3-Parametern (OK - bereits übergeben)
User A: Nächster Request würde Llama3 nutzen (unbeabsichtigt)
```

- Settings-Änderungen betreffen alle Nutzer sofort
- Laufende Requests sind sicher (Parameter bereits ans Backend übergeben)
- Neue Requests von User A würden User B's Settings nutzen

### 📊 Speicher & Session-Verwaltung

**Session-Speicherung:**
- Sessions im RAM gespeichert (plain dict standardmäßig, kein Redis)
- **Kein automatisches Ablaufen** - Sessions bleiben im Speicher bis zum Server-Neustart
- Leere Sessions sind klein (~1-5 KB pro Session)
- **Kein Problem**: Selbst 100 leere Sessions = ~500 KB RAM

**Chat-Verlauf:**
- Nutzer die regelmäßig ihren Chat-Verlauf löschen halten die Speichernutzung niedrig
- Volle Konversationen (50+ Nachrichten) nutzen mehr RAM, sind aber handhabbar
- History-Kompression (70% Trigger) hält Context handhabbar

### 🔧 Design-Begründung

**Warum ist die Backend-Konfiguration global?**

AIfred ist für lokale Hardware mit begrenzten Ressourcen ausgelegt:
- **Einzelne GPU**: Kann nur ein Modell gleichzeitig effizient laufen lassen
- **VRAM-Beschränkungen**: Verschiedene Modelle pro Nutzer laden würde VRAM überschreiten
- **Hardware ist single-user-orientiert**: Alle Nutzer müssen sich das konfigurierte Backend/Modelle teilen

**Das ist beabsichtigt** - das System ist optimiert für:
- **Primärer Use-Case**: 1 Nutzer, gelegentlich 2-3 Nutzer
- **Geteilte Hardware**: Alle nutzen dieselbe GPU/Modelle
- **Root-Kontrolle**: Administrator (du) verwaltet Einstellungen, andere nutzen das System wie konfiguriert

### 🛡️ Empfehlungen für Multi-User-Setup

1. **Nutzungsregeln etablieren:**
   - Einen Admin (Root-User) bestimmen, der die Einstellungen verwaltet
   - Andere Nutzer sollten Backend/Modell-Einstellungen nicht ändern
   - Kommunizieren, wenn kritische Einstellungen geändert werden

2. **Sichere gleichzeitige Nutzung:**
   - ✅ Mehrere Nutzer können gleichzeitig Requests senden
   - ✅ Jeder Nutzer bekommt seine eigene Antwort und Chat-Verlauf
   - ⚠️ Vermeide Einstellungs-Änderungen während andere das System aktiv nutzen

3. **Erwartetes Verhalten:**
   - Nutzer sehen dieselben verfügbaren Modelle (geteiltes Dropdown)
   - Einstellungs-Änderungen synchronisieren sich zwischen Browser-Tabs innerhalb 1-2 Sekunden (via `settings.json` Polling)
   - **UI-Sync-Verzögerung**: Modell-Dropdown aktualisiert sich visuell möglicherweise erst beim Klicken/Öffnen (bekannte Reflex-Einschränkung)
   - Multi-Agent-Modus und andere einfache Einstellungen synchronisieren sich sofort und sichtbar
   - Das ist **by design** für Single-GPU-Hardware

### 🚫 Was AIfred NICHT ist

- ❌ **Kein Multi-Tenant-SaaS**: Keine nutzer-spezifischen Accounts, Quotas oder isolierte Ressourcen
- ❌ **Nicht für >5 gleichzeitige Nutzer ausgelegt**: Request-Queue würde langsam werden
- ❌ **Nicht für nicht-vertrauenswürdige Nutzer**: Jeder Nutzer kann globale Einstellungen ändern (keine Permissions/Rollen)

### ✅ Was AIfred IST

- ✅ **Persönlicher KI-Assistent** für Heim-/Büronutzung
- ✅ **Familien-freundlich**: 2-3 Familienmitglieder können es gleichzeitig ohne Probleme nutzen
- ✅ **Developer-fokussiert**: Root-User hat volle Kontrolle, andere nutzen es wie konfiguriert
- ✅ **Hardware-optimiert**: Macht beste Nutzung der einzelnen GPU für alle Nutzer

**Zusammenfassung**: AIfred funktioniert gut für kleine Gruppen (2-3 Nutzer), die Einstellungs-Änderungen koordinieren, ist aber nicht geeignet für großskalige Multi-User-Deployments oder nicht-vertrauenswürdige Nutzer-Zugriffe.

---

## 🛠️ Development

### Debug Logs
```bash
tail -f data/logs/aifred_debug.log
```

### Code-Qualitätsprüfung
```bash
# Syntax-Check
python3 -m py_compile aifred/DATEI.py

# Linting mit Ruff
source venv/bin/activate && ruff check aifred/

# Type-Checking mit mypy
source venv/bin/activate && mypy aifred/ --ignore-missing-imports
```

## ⚡ Performance-Optimierung

### Ollama: OLLAMA_NUM_PARALLEL=1 (Kritisch für Single-User)

**Problem:** Ollamas Standard `OLLAMA_NUM_PARALLEL=2` **verdoppelt den KV-Cache** für einen ungenutzten zweiten Parallel-Slot. Das verschwendet ~50% des GPU-VRAM.

**Auswirkung:**
- Mit PARALLEL=2: 30B Modell passt ~111K Context (mit CPU-Offload)
- Mit PARALLEL=1: 30B Modell passt ~222K Context (reines GPU, kein Offload)

**Lösung:** Setze `OLLAMA_NUM_PARALLEL=1` in der Ollama systemd-Konfiguration:

```bash
# Override-Verzeichnis erstellen
sudo mkdir -p /etc/systemd/system/ollama.service.d/

# Override-Datei erstellen
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_NUM_PARALLEL=1"
EOF

# Änderungen anwenden
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

**Wann PARALLEL=1 verwenden:**
- Single-User Setups (Home Server, persönliche Workstation)
- Maximales Context-Fenster für Research/RAG-Tasks benötigt

**Wann PARALLEL=2+ beibehalten:**
- Multi-User Server mit gleichzeitigen Anfragen
- Load-Balancing Szenarien

Nach dieser Änderung **Modelle neu kalibrieren** in der UI, um den freigewordenen VRAM zu nutzen.

### llama.cpp vs Ollama Performance-Vergleich

Benchmarks mit Qwen3-30B-A3B Q8_0 auf 2× Tesla P40 (48 GB VRAM gesamt):

| Metrik | llama.cpp | Ollama | Vorteil |
|--------|:---------:|:------:|:-------:|
| TTFT (Time to First Token) | 1,1s | 1,5s | llama.cpp -27% |
| Generierungsgeschwindigkeit | 39,3 tok/s | 27,4 tok/s | llama.cpp +43% |
| Prompt-Verarbeitung | 1.116 tok/s | 862 tok/s | llama.cpp +30% |
| Intent-Erkennung | 0,8s | 0,7s | ähnlich |

**Wann llama.cpp wählen:**
- Maximale Generierungsgeschwindigkeit und Durchsatz
- Multi-GPU-Setups (volle Tensor-Split-Kontrolle)
- Große Kontextfenster (direkte VRAM-Kalibrierung)
- Produktiv-Deployments wo jedes tok/s zählt

**Wann Ollama wählen:**
- Schnelles Setup und Experimentieren
- Automatisches Modell-Management (`ollama pull`)
- Einfachere Konfiguration für Einsteiger

---

## 🔨 Troubleshooting

### Häufige Probleme

#### HTTP ReadTimeout bei Research-Anfragen
**Problem**: `httpx.ReadTimeout` nach 60 Sekunden bei großen Recherchen
**Lösung**: Timeout ist bereits auf 300s erhöht in `aifred/backends/ollama.py`
**Falls weiterhin Probleme**: Ollama Service neustarten mit `systemctl restart ollama`

#### Service startet nicht
**Problem**: AIfred Service startet nicht oder stoppt sofort
**Lösung**:
```bash
# Logs prüfen
journalctl -u aifred-intelligence -n 50
# Ollama Status prüfen
systemctl status ollama
```

#### Restart-Button funktioniert nicht
**Problem**: Restart-Button in Web-UI ohne Funktion
**Lösung**: Polkit-Regel prüfen in `/etc/polkit-1/rules.d/50-aifred-restart.rules`

---

## 📚 Dokumentation

Weitere Dokumentation im `docs/` Verzeichnis:
- [Security-Architektur](docs/de/architecture/security.md)
- [Scheduler & Proaktive Features](docs/de/architecture/scheduler.md)
- [Plugin-Entwicklung](docs/en/guides/plugin-development.md) (mit Templates)
- [Message Hub Architektur](docs/de/architecture/message-hub.md)
- [LLM-Aufruf-Architektur](docs/en/architecture/llm-call.md)
- [llama.cpp + llama-swap Setup Guide](docs/en/guides/llamacpp-setup.md)
- [Deployment Guide](docs/en/guides/deployment.md)
- [Tensor Split Benchmark: Speed vs. Full Context](docs/en/benchmarks/tensor-split.md)

---

## 📄 Lizenz

PolyForm Noncommercial License 1.0.0 - siehe [LICENSE](LICENSE)

Frei für persönliche, bildungsbezogene und nicht-kommerzielle Nutzung. Kommerzielle Nutzung erfordert eine separate Lizenz vom Autor.

---

## ☕ Unterstützung

Wenn dir dieses Projekt gefällt, kannst du mich unterstützen:

<a href="https://ko-fi.com/peuqui" target="_blank"><img src="https://storage.ko-fi.com/cdn/kofi2.png?v=6" alt="Unterstütze mich auf Ko-fi" height="50"></a>