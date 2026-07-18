# AIfred-Einrichtungsanleitung

Anleitung zur Einrichtung einer neuen AIfred-Installation mit dem llama.cpp-Backend (llama-swap).

**Zuletzt aktualisiert:** 18.07.2026

> **TL;DR – der schnellste Weg:** `./scripts/install-all.sh` aus einem frischen
> Klon kümmert sich in einem Durchgang um Abhängigkeiten, venv, Playwright, den Reflex-Routing-
> Patch, optionale systemd-Dienste, `.env`, den `bge-m3`-Embedding-Pull
> und einen ersten Whitelist-Benutzer. Die folgenden Abschnitte beschreiben
> denselben Ablauf **manuell** für die Fehlersuche und für nicht standardmäßige Konfigurationen
> (Multi-GPU-Systeme, Kameraüberwachung, Koexistenz mit vLLM).

---

## Überblick

AIfred nutzt **llama-swap** als Proxy-Daemon für llama.cpp. llama-swap verwaltet
mehrere Modelle und lädt sie bei Bedarf. Der **autoscan**-Mechanismus erkennt neue
Modelle automatisch und konfiguriert sie, ohne dass manuelle YAML-Bearbeitung nötig ist.

```
User <-> AIfred (Reflex web app) <-> llama-swap (:11435) <-> llama-server (per model)
```

---

## 1. Voraussetzungen

### Hardware
- NVIDIA-GPU mit CUDA-Unterstützung (Compute Capability >= 6.1, Pascal oder neuer)
- Empfohlen: >= 24 GB VRAM für brauchbare Modellgrößen

### Software
- Linux mit systemd (Ubuntu/Debian empfohlen)
- CUDA Toolkit >= 12.0
- Python 3.10+
- Git

---

## 2. „llama.cpp“ kompilieren

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build -j$(nproc)

# Verify the binary exists
ls ~/llama.cpp/build/bin/llama-server
```

> **Hinweis:** Der Autoscan erwartet die Binärdatei standardmäßig unter `~/llama.cpp/build/bin/llama-server`.
> Befindet sie sich an einem anderen Ort, füge einen bestehenden YAML-Eintrag mit dem korrekten
> Pfad hinzu – der Autoscan liest den Pfad zur Binärdatei aus den vorhandenen Konfigurationseinträgen aus.

---

## 3. llama-swap installieren

```bash
# Download the binary from GitHub Releases into ~/bin/
# https://github.com/mostlygeek/llama-swap/releases
mkdir -p ~/bin
wget -O ~/bin/llama-swap https://github.com/mostlygeek/llama-swap/releases/latest/download/llama-swap-linux-amd64
chmod +x ~/bin/llama-swap

# Create the config directory
mkdir -p ~/.config/llama-swap
```

```bash
# Create the config directory — the autoscan creates the config file itself
mkdir -p ~/.config/llama-swap
```

> **Hinweis:** Der Autoscan erstellt `config.yaml` von Grund auf neu, sobald Modelle gefunden werden.
> Ein leerer Platzhalter wird nur benötigt, wenn du llama-swap startest, bevor du Modelle heruntergeladen hast.

---

## 4. AIfred einrichten

```bash
git clone https://github.com/Peuqui/AIfred-Intelligence ~/Projekte/AIfred-Intelligence
cd ~/Projekte/AIfred-Intelligence

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 5. systemd-Dienste einrichten

Für die AIfred-Dienste (chromadb + intelligence + optionaler
corpus-server) nutze das Installationsskript – es ist update-sicher:

```bash
sudo ./scripts/install-services.sh                 # install or update,
                                                   # backs up modified
                                                   # files before overwrite
./scripts/install-services.sh --dry-run            # show what WOULD change
                                                   # — no sudo, no writes
sudo ./scripts/install-services.sh --no-overwrite  # keep existing service
                                                   # files (preserves
                                                   # machine-specific
                                                   # tweaks)
```

Das Skript meldet pro Datei `= Unverändert`, `♻️ Aktualisiert`, `✅ Neu installiert`
oder `🛡 Behalten`. `daemon-reload` und `restart` werden nur ausgelöst, wenn
sich eine Unit tatsächlich geändert hat – erneute Ausführungen auf einem sauberen System haben keine Auswirkungen.

Das Installationsskript rendert `systemd/aifred-intelligence.service` (und die
chromadb-/Corpus-Einheiten) in `/etc/systemd/system/`, ersetzt die
tatsächlichen Benutzer- und Projektpfade, lädt systemd neu und aktiviert die Einheiten. Dies
sind **Dienste auf Systemebene** (`WantedBy=multi-user.target`, laufen als
`User=<du>`) – verwalte sie mit `sudo systemctl`, nicht mit `systemctl --user`.

> **Tipp:** Für `enable`/`disable` und das Bearbeiten von Units ist immer `sudo` erforderlich (sie
> schreiben in `/etc/systemd/system/`). Die Laufzeitbefehle `restart`,
> `stop` und `status` können **ohne** `sudo` ausgeführt werden, wenn du eine PolKit-Regel hinzufügst,
> die es deinem Benutzer erlaubt, diese spezifischen Units zu verwalten – praktisch für die
> häufigen `restart llama-swap` / `restart aifred-intelligence` während der
> Feinabstimmung. Ohne eine solche Regel musst du ihnen `sudo` voranstellen.

Die AIfred-Einheit führt Reflex direkt über das Python-venv aus:

```
ExecStartPre=/bin/bash <project>/scripts/patch-vite-config.sh
ExecStart=<project>/venv/bin/python -m reflex run \
    --frontend-port 3002 --backend-port 8002 --backend-host 0.0.0.0
```

### llama-swap-Dienst (mit Autoscan)

llama-swap ist **nicht** Teil von `install-services.sh` – es handelt sich um eine separate
Unit auf Systemebene, die du einmalig unter `/etc/systemd/system/` erstellst. Die
Binärdatei befindet sich in `~/bin/llama-swap` (aus Abschnitt 3):

```bash
sudo tee /etc/systemd/system/llama-swap.service > /dev/null << EOF
[Unit]
Description=llama-swap - LLM Model Proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Group=$USER
ExecStartPre=$HOME/Projekte/AIfred-Intelligence/venv/bin/python \
    $HOME/Projekte/AIfred-Intelligence/scripts/llama-swap-autoscan.py
ExecStart=$HOME/bin/llama-swap \
    --config $HOME/.config/llama-swap/config.yaml \
    --listen :11435 --watch-config
Restart=on-failure
RestartSec=5
TimeoutStartSec=300
Environment=PATH=/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin
Environment=LD_LIBRARY_PATH=/usr/local/cuda/lib64
Environment=CUDA_DEVICE_ORDER=FASTEST_FIRST
Environment=GGML_CUDA_GRAPH_OPT=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable llama-swap
```

---

## 6. Modelle hinzufügen

Der Autoscan erkennt Modelle aus drei Quellen automatisch. Nachdem du ein Modell hinzugefügt hast,
starte llama-swap neu – dann ist es konfiguriert und einsatzbereit.

```bash
sudo systemctl restart llama-swap
```

### Option A: Ollama

```bash
ollama pull qwen3:14b
sudo systemctl restart llama-swap
```

Der Autoscan führt Folgendes durch:
1. Liest das Ollama-Manifest, um den GGUF-Blob zu finden
2. Einen Symlink `~/models/Qwen3-14B-Q8_0.gguf` → Ollama-Blob erstellen
3. Einen 6-sekündigen Kompatibilitätstest mit llama-server durchführen
4. Einen Eintrag in `~/.config/llama-swap/config.yaml` schreiben
5. Die Liste `groups.main.members` in der Konfiguration aktualisieren

> **Einschränkung:** Über Ollama abgerufene Vision-Language-Modelle (VL) (z. B. `qwen3-vl`)
> sind als **Vision**-Modell nicht mit llama-server kompatibel. Ollamas GGUF-
> Blobs lassen den von llama.cpp benötigten MRoPE-Metadaten-Schlüssel weg, und der
> `--mmproj`-Pfad von llama.cpp ist für Qwen3-VL derzeit ohnehin unzuverlässig.
> Der Autoscan erkennt das automatisch und fügt das Modell mit einem Hinweis zur
> Skip-Liste hinzu. **Vision-Inferenz läuft auf einem dedizierten Ollama-VLM-
> Dienst** (`ollama-vlm.service`) – siehe Abschnitt 10. llama-swap stellt solche Modelle ausschließlich
> als LLMs im Klartext bereit.

### Option B: HuggingFace

```bash
# Install the HF CLI (one-time, includes the 'hf' command)
pip install huggingface_hub

# Download a model (lands in ~/.cache/huggingface/hub/)
hf download Qwen/Qwen3-14B-GGUF --include "Qwen3-14B-Q8_0.gguf"

# VL model with projector (mmproj)
hf download Qwen/Qwen3-VL-8B-Instruct-GGUF \
    --include "Qwen3-VL-8B-Instruct-Q4_K_M.gguf" "mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

sudo systemctl restart llama-swap
```

Der Autoscan führt Folgendes durch:
1. Durchsucht `~/.cache/huggingface/hub/` nach GGUFs im aktiven Snapshot
2. Einen Symlink `~/models/Qwen3-14B-Q8_0.gguf` → HF-Cache-Pfad erstellen
3. Den Kompatibilitätstest ausführen und den YAML-Eintrag schreiben
4. Die Liste `groups.main.members` in der Konfiguration aktualisieren

Wenn eine passende `mmproj-*.gguf`-Datei im selben HF-Snapshot vorhanden ist, kann der
kann der YAML-Eintrag automatisch `--mmproj` enthalten. Beachte jedoch, dass der
llama-server-Vision-Pfad für aktuelle Qwen3-VL-Builds unzuverlässig ist – der
unterstützte Vision-Pfad ist der dedizierte Ollama-VLM-Dienst (siehe Abschnitt 10).

### Option C: Manuelles GGUF

```bash
# Drop the file directly into ~/models/
cp /path/to/Model.gguf ~/models/

# Or create a symlink
ln -s /path/to/Model.gguf ~/models/Model.gguf

sudo systemctl restart llama-swap
```

### Warum ~/models/?

Dieses Verzeichnis dient als **einheitlicher Namensraum** für alle Modellquellen:
- Ollama-Blobs haben SHA256-Hash-Namen (`sha256-6335adf...`) – diese lassen sich nicht direkt
  in der YAML-Konfiguration verwenden
- HuggingFace-Cache-Pfade sind lang und verschachtelt
  (`~/.cache/huggingface/hub/models--Qwen--Qwen3-14B-GGUF/snapshots/{hash}/...`)
- Manuelle GGUFs benötigen einen festgelegten Speicherort

Der Autoscan durchsucht immer `~/models/` und schreibt `~/models/Name.gguf` in die
YAML-Datei. Alle drei Quellen werden über Symlinks in diesen Namensraum geleitet.

---

## 7. Starten und überprüfen

```bash
# Start llama-swap (autoscan runs as part of startup)
sudo systemctl start llama-swap

# Watch the autoscan output
sudo journalctl -u llama-swap -b | head -60

# Check available models
curl -s http://localhost:11435/v1/models | python3 -m json.tool

# Start AIfred
sudo systemctl start aifred-intelligence
```

Typische Ausgabe des Autoscans:
```
=== llama-swap Autoscan ===

Scanning Ollama models...
  + Symlink: Qwen3-14B-Q8_0.gguf → sha256-6335adf...
  = Exists:  Qwen3-8B-Q4_K_M.gguf
  ~ Skip:    nomic-embed-text-v2-moe (embedding model)
  3 Ollama models found, 1 new symlinks created

Scanning HuggingFace cache...
  No HuggingFace cache found or empty.

Cleaning up...
  Nothing to clean up

Scanning ~/models/ for GGUFs...
  Found 5 GGUFs, 1 new

Testing new models for llama-server compatibility...
  ✓ Qwen3-14B-Q8_0 (OK)

Updating llama-swap-config.yaml...
  + Added: Qwen3-14B-Q8_0 (native context: 40960)

Updating VRAM cache...
  + Added: Qwen3-14B-Q8_0

Groups updated: main → [Qwen3-14B-Q8_0, Qwen3-8B-Q4_K_M]

Done. 1 added, 1 VRAM cache entries added.
```

### Auf die Web-UI zugreifen

AIfred läuft als **zwei Prozesse** hinter einem einzigen Port, die ein Reverse-Proxy
miteinander verbindet:

| Prozess | Standardport | Bietet |
|---------|-------------|--------|
| Reflex **Frontend** (Node) | `3002` | die App-Seiten + WebSocket-Statuskanal |
| Reflex **Backend** (Granian/FastAPI) | `8002` | `/api/*` (REST, Casus-Frames, Audio-SSE), `/_upload/*` (Bilder, Face-Crops, Dokumente), `/_event` |

**Der Frontend-Port allein reicht nicht aus.** Wenn du die App direkt unter
`http://<host>:3002/aifred/` öffnest, werden die Seiten geladen und der WebSocket funktioniert, aber jede
`/api/*`- und `/_upload/*`-Anfragen einen 404-Fehler – daher bleiben Kamera-Miniaturansichten, das Vigilantia-
Live-Modal, Casus-Vorschauen und die Audiowiedergabe leer. Diese Routen existieren nur
im Backend, und nur ein Reverse-Proxy vor beiden Prozessen macht
sie unter einem gemeinsamen Origin erreichbar.

**Empfohlen: ein Reverse-Proxy (nginx/Caddy)**, der anhand des Pfadpräfixes zu den
beiden Upstreams weiterleitet. Minimaler nginx-Sketch (generisch – ersetze deinen eigenen Host,
Ports und, falls gewünscht, TLS/Authentifizierung):

```nginx
server {
    listen 80;
    server_name your-host.example;   # or a LAN IP

    # App pages + WebSocket → frontend
    location /aifred/ {
        proxy_pass http://127.0.0.1:3002/aifred/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;      # WebSocket
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    # REST API, uploads, server-sent events → backend
    location ~ ^/(api|_upload|_event) {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # `/_upload/*` is cookie-gated by AIfred's own login; add
        # auth_basic here as a second factor if the proxy is exposed.
    }
}
```

Sobald der Proxy eingerichtet ist, öffne **`http://your-host.example/aifred/`** (ohne Port).
Die statischen Einbindungen unter `/_upload/*` sind zusätzlich durch die Web-Anmeldung
(`AuthenticatedStaticFiles`) mit Cookies geschützt, sodass nur die Freigabelinks unter
`/_upload/html_preview` ohne Login erreichbar sind.

> **Schnelle lokale Überprüfung ohne Proxy:** Die App ist unter `:3002` für
> alles nutzbar, was über den WebSocket läuft (Chat, Einstellungen), aber behandle fehlende
> Bilder/Audiodateien dort wie erwartet, nicht als Fehler – greife über den Proxy darauf zu, um
> die vollständige Benutzeroberfläche zu sehen.

---

## 8. Modelle entfernen

Wenn ein Modell gelöscht wird (über `ollama rm`, durch Entfernen der GGUF-Datei oder durch Leeren
des HuggingFace-Caches), räumt der Autoscan beim nächsten
llama-swap-Neustart automatisch auf:

```bash
ollama rm qwen3:8b
sudo systemctl restart llama-swap
```

Der Autoscan führt Folgendes durch:
1. Entfernt defekte Symlinks in `~/models/`
2. Konfigurationseinträge entfernen, deren `--model`-Pfad nicht mehr existiert
3. Veraltete Einträge aus der Kompatibilitäts-Skip-Liste entfernen
4. Verwaiste VRAM-Cache-Einträge entfernen
5. Die Liste `groups.main.members` aktualisieren

Beispiel für die Bereinigungsausgabe:
```
Cleaning up...
  - Removed dead symlink: Qwen3-8B-Q8_0.gguf
  - Removed: qwen3-8b-q8_0
  1 dead symlink(s) removed
  1 stale model(s) removed from config

Groups updated: main → [Qwen3-14B-Q8_0]
  - VRAM cache: removed qwen3-8b-q8_0
  1 stale VRAM cache entry/entries removed

Done. 1 removed.
```

Keine manuelle YAML-Bearbeitung erforderlich.

---

## 9. VRAM-Kalibrierung

Neue Modelle werden mit ihrem **nativen Kontext** aus den GGUF-Metadaten hinzugefügt.
Dieser ist oft größer als das, was tatsächlich in den VRAM passt. Die Kalibrierung ermittelt
das tatsächliche Maximum.

So führst du die Kalibrierung in der AIfred-Benutzeroberfläche durch:

1. Wähle das neue Modell in AIfred aus
2. Klicke neben der Modellauswahl auf **„Kalibrieren“**
3. Wähle die gewünschten Varianten über den **2D-Matrix-Picker** aus:
   - Zeilen = VLM-Auswahl (Kein VLM / Vigilantia 4B / Vigilantia 8B)
   - Spalten = TTS-Engines (Kein TTS / Qwen3-TTS / XTTS / MOSS-TTS / Fish-Speech)
   - Jede angekreuzte Zelle wird zu einem separaten `<base>-vlm-<key>-tts-<engine>`
 llama-swap-Profil, das der Chat-Path-Resolver automatisch übernimmt
4. Klicke auf **„Kalibrierung starten“**. Die Matrix zeigt pro Zelle drei Zustände an:
   - 🟢 grüner Punkt — kalibriert
   - 🔴 roter Punkt — versucht, aber fehlgeschlagen (mit der Maus darüberfahren, um den Grund zu sehen)
   - leer — noch nie versucht

Was im Hintergrund abläuft:

- **Greedy-Kaskade**: Fülle zuerst die schnellste Rechenklasse, weiche dann auf die
  nächste aus, minimiere die Anzahl aktiver GPUs
- **Stress-Burn-in** bei der ersten TTS/VLM-Nutzung: Eine zweisprachige TTS-Synthese im Worst-Case-Szenario
  Synthese-Schleife und eine VLM-Kontext-Füllung zum Vorwärmen messen den Spitzen-VRAM-Verbrauch unter
  Last. Ergebnisse werden in `data/tts_vram_cache.json` /
  `data/vlm_vram_cache.json` zwischengespeichert – nachfolgende Kalibrierungen nutzen die Messwerte wieder
- **Side-Channel-Kapazitätsüberwachung**: Bevor ein `tts-engine + vlm`-
  Kombinationsprofil geschrieben wird, prüft der Kalibrator, ob beide Reserven auf die
  gemeinsam genutzte Side-Channel-GPU passen. Kombinationen, die zur Laufzeit einen OOM auslösen würden, werden
  mit einem roten Punkt abgelehnt
- **Bias-verfolgte binäre Suche**: Wenn `llama-fit-params` durchgehend
  deaktiviert ist (typisch bei MoE-Modellen), wird der Bias über alle Probes hinweg verfolgt und
  in die mathematische Projektion zurückgeführt, sodass die Suche bereits nach 3–5
  Proben statt bei über 25 konvergiert
- Die Endergebnisse werden in `data/model_vram_cache.json` und als Profil-
  Einträge in `~/.config/llama-swap/config.yaml` gespeichert

> **Strategie-Referenz (SSOT):** [calibration-strategy.md](../architecture/calibration-strategy.md)

Ohne Kalibrierung funktioniert das Modell trotzdem – es läuft mit dem nativen
Kontext. Wenn dieser den VRAM-Speicherplatz überschreitet, schlägt die erste Anfrage mit einem OOM-Fehler
fehl.

---

## 10. Vision-Einrichtung (optional)

Die Vision-Pipeline ist **standardmäßig deaktiviert**. Schalte sie ein, wenn du
Bildanalyse im Chat, VLM-Abfragen auf Abruf über Tools oder das
Vigilantia-Kameraüberwachungs-Plugin nutzen möchtest.

### Hardware

- Eine V4L2-fähige Kamera unter `/dev/video0` (oder einem beliebigen `/dev/video*`) für den
  Webcam-Eingang. USB-UVC-Kameras und integrierte Laptop-Webcams funktionieren einfach
- Für die Gesichtserkennung: Eine NVIDIA-GPU (CUDA Execution Provider) wird
  empfohlen. Nur mit der CPU funktioniert es zwar auch, ist aber deutlich langsamer
- **Mitgliedschaft in der Gruppe `video`**: Das AIfred-Dienstkonto muss in
  der Gruppe `video` sein. Das Skript `install-all.sh` überprüft dies und
  zeigt einen Hinweis zur Behebung an, falls dies fehlt

```bash
groups | grep -qw video || sudo usermod -aG video $USER
# log out + back in (or run 'newgrp video') for the change to take effect
```

### Ein VLM (Vision-Language Model) über Ollama abrufen

Die VLM-Inferenz läuft auf Ollama (der Pfad `--mmproj` in llama.cpp ist derzeit
für Qwen3-VL unzuverlässig – siehe die Architekturhinweise). Lade eines der
kalibrierten VLMs herunter:

```bash
ollama pull qwen3-vl:4b-instruct-q8_0    # ~6.5 GB VRAM, fast
ollama pull qwen3-vl:8b-instruct-q8_0    # ~11 GB VRAM, more accurate
```

### In der Benutzeroberfläche aktivieren

1. Einstellungen → Vision → setze `vision_mode` auf:
   - `off` – deaktiviert (Standard)
   - `on-demand` – VLM wird nur geladen, wenn ein Vision-Tool aufgerufen wird
   - `live` – VLM bleibt im VRAM resident (geringere Latenz, höhere
   Leerlaufkosten)
2. Wähle das aktive VLM-Modell unter „Einstellungen“ → „Vision“ → „Modell“ aus
3. (Optional) Konfiguriere die Gesichtserkennung:
   - Einstellungen → Vision → Gesichtserkennung → Ausführungsanbieter
 (CUDA / CPU / CoreML)
   - Schwellenwert für die Klassifizierung „bekannt“ vs. „unsicher“

### Kalibriere das LLM mit VLM-Unterstützung

Wenn `vision_mode` auf `on-demand` oder `live` gesetzt ist, muss das LLM-Profil
VRAM auf der Side-Channel-GPU für den VLM-Container reservieren. Führe die
Kalibrierung (Abschnitt 9) erneut durch, wobei das Kästchen für **Vigilantia 4B** oder **Vigilantia 8B**
– das erzeugt ein `<base>-vlm-<key>`-Profil, und der
Resolver wählt es automatisch aus, wenn die Bildverarbeitung aktiv ist.

---

## 11. Einrichtung von Vigilantia (Kameraüberwachung) (optional)

Wird auf die Bildverarbeitungspipeline aufgesetzt. Verwandelt AIfred in einen Agenten zur kontinuierlichen
Überwachung mit Bewegungserkennung, Gesichtserkennung und Ereignisüberprüfung.

### Ersteinrichtung

1. Aktiviere das **Vigilantia**-Kanal-Plugin im Plugin-Manager
2. Starte die Message-Hub-Worker neu (es wird beim Start geladen)
3. Öffne das **Casus**-Modal – dort werden erkannte Kameraquellen aufgelistet

### Gesichter registrieren (Personarium)

Die Gesichtserkennungs-Pipeline stuft ein Gesicht nur dann als „bekannt“ ein, wenn du es
zuvor **registriert** hast. Ohne Registrierung wird jedes Gesicht als
`unbekanntes` Ereignis angezeigt.

1. Mach einen Schnappschuss eines Bildes von einer Kamera mit einem deutlich erkennbaren Gesicht
2. Öffne das **Personarium**-Modal
3. Multi-Pose-Assistent: Erfasse eine Frontalaufnahme + 4 Winkel
4. Weise einen Namen + (optional) eine Gruppe zu
5. Die Gesichtsvektoren werden im SQLite-Speicher abgelegt; beim nächsten Watcher-Durchlauf
   werden übereinstimmende Gesichter als `bekannt` klassifiziert

**Kosten beim ersten Lauf:** Beim ersten Aufruf lädt `insightface` das
`buffalo_l`-Modell (~280 MB) in `~/.insightface/models/` herunter. Nachfolgende
Läufe sind schnell.

### Einen Watcher starten

```
LLM: vision_start_watch(source="webcam0", motion=true, face=true, vlm_on_motion=false)
```

Oder über die Casus-Benutzeroberfläche: Quelle auswählen → „Watcher starten“. Der Watcher
läuft im Worker-Prozess des Message Hubs, sodass er **auch bei einer Unterbrechung der Browserverbindung
weiterläuft**.

### Schwellenwerte konfigurieren

Einstellungen → Vision → Vigilantia:

- `motion.min_area_ratio` — Anteil des Bildes, der sich ändern muss,
  bevor ein Bewegungsereignis ausgelöst wird (Standard 0,02 = 2 %)
- `motion.warmup_frames` – Anzahl der Frames, um den Hintergrund zu erfassen, bevor
  das Ereignis ausgelöst wird (Standard 10)
- `min_event_interval_sec` – Entprellzeit zwischen den Ereignissen (Standard 1 s).
  **Pro Kamera** im Kamera-Editor einstellbar („Min. Event-Abstand"); die
  Live-Vorschau hat im Kopfbereich ihre eigene, unabhängige Drossel
- `save_event_frames` — Speichert bei jedem Ereignis den Frame als JPEG
- `face_detect.threshold_known` — Kosinus-Ähnlichkeit, ab der ein Gesicht
  als `bekannt` gilt (Standard 0,6)
- `face_detect.threshold_unsure` — unterhalb von `known`, aber oberhalb dieses Wertes →
  `unsure` (Standard 0,5). Darunter → `unknown`
- `events.retention_days_*` — Aufbewahrungsdauer pro Ereignistyp

### Den Casus-Ereignis-Browser nutzen

Das **Casus**-Modal ist das zentrale Tool zur Ereignisüberprüfung:

- Nach Typ filtern (Bewegung / Gesicht_bekannt / Gesicht_unsicher / Gesicht_unbekannt / VLM-Analyse)
- Nach Quelle, Gesichts-ID oder Zeit filtern
- **VLM-Analyse für einzelne Ereignisse**: Klicke auf ein beliebiges Ereignis → „Mit VLM analysieren“
  — führt das konfigurierte VLM auf dem gespeicherten Bild aus
- **VLM-Massenanalyse**: Wähle N Ereignisse aus → Ein Hintergrundprozess führt die
  VLM für jedes einzelne durch, mit Fortschrittsanzeige und Abbruchoption. Eine VRAM-Vorabprüfung bricht den Vorgang sauber ab,
  wenn nicht genügend Speicherplatz für den konfigurierten VLM-Stapel vorhanden ist
- **Cluster-Modus umschalten**: Fasst nahezu identische Ereignisse (pHash-basiert)
  zu einer Karte pro Cluster zusammen – nützlich, wenn ein im Wind schwankender Ast
  sonst innerhalb von 10 Minuten 200 Bewegungsereignisse erzeugen würde

---

## 12. Fehlerbehebung

### Das Modell erscheint nicht in AIfred

```bash
# Check the YAML
cat ~/.config/llama-swap/config.yaml

# Check autoscan output
sudo journalctl -u llama-swap -b | grep -A5 "Autoscan"

# Run autoscan manually
source ~/Projekte/AIfred-Intelligence/venv/bin/activate
python ~/Projekte/AIfred-Intelligence/scripts/llama-swap-autoscan.py
```

### Das Modell ist in der Skip-Liste gelandet

```bash
cat ~/.config/llama-swap/autoscan-skip.json
# Remove the entry to re-test after a llama.cpp update:
nano ~/.config/llama-swap/autoscan-skip.json
sudo systemctl restart llama-swap
```

### llama-server-Binärdatei nicht gefunden

Der Autoscan liest den Pfad zur Binärdatei aus vorhandenen YAML-Einträgen aus. Wenn noch keine Einträge vorhanden sind,
greift er auf `~/llama.cpp/build/bin/llama-server` zurück. Befindet sich die Binärdatei
an einem anderen Ort, füge einen temporären Eintrag mit dem korrekten Pfad hinzu:

```yaml
# ~/.config/llama-swap/config.yaml
models:
  _dummy:
    cmd: /your/path/to/llama-server --port ${PORT} --model /dev/null
    ttl: 1
```

Führe den Autoscan einmal aus und entferne anschließend den `_dummy`-Eintrag.

### OOM-Absturz / Kontext zu groß

```bash
# Run calibration in AIfred UI, or reduce the context manually:
nano ~/.config/llama-swap/config.yaml
# Adjust the -c parameter for the affected model
sudo systemctl restart llama-swap
```

---

## Verwandte Dokumente

- [llamacpp-setup.md](llamacpp-setup.md) — Hardware-Benchmarks, Leistungsoptionen,
  Multi-GPU-Konfiguration, Details zu Flash Attention
