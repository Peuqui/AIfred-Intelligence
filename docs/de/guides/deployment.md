# AIfred-Einrichtungsanleitung

> **English version:** [deployment.md](../../en/guides/deployment.md)

Anleitung zur Einrichtung einer neuen AIfred-Installation mit dem llama.cpp-Backend (llama-swap).

**Zuletzt aktualisiert:** 25.09.2026

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

## 2. llama.cpp kompilieren

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

Das Skript meldet pro Datei `= Unchanged`, `♻️ Updated`, `✅ Newly installed`
oder `🛡 Kept`. `daemon-reload` wird nur ausgelöst, wenn sich eine Unit geändert hat, und ein
Dienst wird nur neu gestartet, wenn sich seine eigene Unit oder sein Drop-in geändert hat – erneute
Ausführungen auf einem sauberen System haben keine Auswirkungen.

| Unit | Startet |
|---|---|
| `aifred-chromadb.service` | `docker compose up -d chromadb searxng` — Vector Store + Websuche |
| `aifred-intelligence.service` | die Reflex-App (Frontend `3002`, Backend `8002`) |
| `aifred-corpus-server.service` | optionale Korpus-Such-API (`127.0.0.1:8005`, für `deploy/corpus/`) |

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

Bearbeite die Units unter `/etc/systemd/system/` nicht von Hand – die Vorlagen in
`systemd/` enthalten die Platzhalter `__USER__` / `__PROJECT_DIR__` / `__DOCKER_BIN__`,
die nur das Installationsskript ausfüllt. Ändere die Vorlage und führe
`sudo ./scripts/install-services.sh` erneut aus. Details: [systemd/README.md](../../../systemd/README.md).

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

Passe die beiden Pfade `$HOME/Projekte/AIfred-Intelligence` an, wenn du das
Repo woanders geklont hast.

### llama-swap-Restart-Helfer

`scripts/llama-swap-restart` ist der Wartungsbefehl nach dem Herunterladen eines
Modells oder dem Bearbeiten der llama-swap-YAML. `install-services.sh` verlinkt ihn nach
`~/bin/llama-swap-restart`. Er geht über `systemctl restart` hinaus:

1. Stoppt `llama-swap.service` und wartet, bis der Dienst wirklich inaktiv ist
2. Beendet übrig gebliebene `llama-server`-Prozesse (SIGTERM, dann SIGKILL)
3. Wartet, bis der GPU-Treiber den VRAM freigegeben hat
4. Löscht verwaiste Lookup-Caches (`~/.cache/llama_lookup_*.bin`), deren Modell
   nicht mehr in `config.yaml` steht
5. Führt `llama-swap-build-config` aus (Spec-Decoding-Flags, TTS-/Vision-Profile)
6. Startet llama-swap (der Autoscan läuft als `ExecStartPre`) und wartet auf `listening`

```bash
hf download <repo> --local-dir ~/models/<name>
llama-swap-restart    # autoscan picks up the new model
```

---

## 5a. Konfiguration und Zugriff

### Umgebung (`.env`)

Secrets und rechnerspezifische Werte gehören in `.env` im Projektverzeichnis
(gitignored; Vorlage: `.env.example`). Der Dienst lädt sie über
`EnvironmentFile=`; die meisten Keys lassen sich auch in der UI setzen (Einstellungen, Plugin
Manager), die sie nach `.env` zurückschreibt.

| Variable | Zweck |
|---|---|
| `AIFRED_ALLOWED_HOST` | Deine externe Domain — wird bei jedem Start zu Vites `allowedHosts` hinzugefügt |
| `INJECT_API_TOKEN` | Token für `/api/chat/inject` (siehe [REST API](rest-api.md)) |
| `WEBHOOK_API_TOKEN` | Token für `/api/agent/trigger` |
| `AIFRED_SESSION_SECRET` | Signiert die Login-Cookies (optional — sonst wird ein Zufalls-Secret persistiert) |
| `LLAMACPP_URL` | llama-swap-URL (Standard `http://localhost:11435/v1`) |
| `AIFRED_FRONTEND_PATH` | URL-Präfix, wenn die App unter einem Unterpfad eines Reverse-Proxys liegt, z. B. `aifred` für `/aifred/` (Standard: keiner) |
| `BACKEND_URL` | Nur ohne Reverse-Proxy: Backend-URL für `/_upload/`, wie der Browser sie sieht |
| `BRAVE_API_KEY`, `TAVILY_API_KEY` | Optionale zusätzliche Such-APIs (SearXNG braucht keinen Key) |
| `ANTHROPIC_API_KEY`, `DASHSCOPE_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY` | Cloud-LLM-Provider |
| `DEEPL_API_KEY` | Translator-Plugin |
| `TELEGRAM_*`, `DISCORD_*`, `EMAIL_*` | Kanal-Plugins — siehe die Anleitungen für [Telegram](telegram-setup.md) / [Discord](discord-setup.md) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google-Suite-Plugin ([OAuth](plugins/oauth.md)) |

`docker/.env` enthält `SEARXNG_SECRET`, einmalig von `install-all.sh` erzeugt.
Ohne diesen Wert verweigert Compose den Start.

### Benutzerverwaltung

Login ist Pflicht. Konten und die Registrierungs-Whitelist werden mit
der Admin-CLI verwaltet:

```bash
./aifred-admin users                          # whitelist (who may register)
./aifred-admin add <username>                 # add to whitelist
./aifred-admin remove <username>              # remove from whitelist
./aifred-admin accounts                       # registered accounts
./aifred-admin create <username> [password]   # account + whitelist in one step
./aifred-admin delete <username> [--sessions] # delete account (optionally its sessions)
```

Ablauf: Namen auf die Whitelist setzen → der User registriert sich in der Web-UI mit
Benutzername + Passwort → loggt sich von jedem beliebigen Gerät aus ein.

### Polkit-Regel (Neustart ohne sudo)

AIfred startet Dienste selbst neu: der Neustart-Button (`aifred-intelligence`),
die Kalibrierung (`llama-swap`) und der Ollama-Restart-Endpoint (`ollama`). Dafür
braucht der Dienst-User eine Polkit-Regel:

```bash
sed 's/YOUR_USER/'"$USER"'/' scripts/polkit/10-aifred.rules \
  | sudo tee /etc/polkit-1/rules.d/10-aifred.rules > /dev/null
sudo chmod 644 /etc/polkit-1/rules.d/10-aifred.rules
```

Die Regel gewährt genau diese drei Units genau diesem User.

### Wie das Frontend das Backend findet

`rxconfig.py` setzt `api_url` auf `http://0.0.0.0:8002`; das Reflex-Frontend
ersetzt `0.0.0.0` durch den Host, von dem die Seite geladen wurde — keine URL-Einstellung
nötig:
- Über HTTPS (Reverse-Proxy) wechselt es auf `https`/`wss` am Standardport
  **443** — der Proxy muss also auch auf 443 lauschen, selbst wenn du die Seite über
  einen anderen Port wie 8443 öffnest.
- Über reines HTTP (z. B. `http://<LAN-IP>:3002`) spricht der Browser direkt mit Port
  **8002** auf diesem Host — deshalb lauscht das Backend auf allen
  Interfaces (`--backend-host 0.0.0.0`). Jede `/api`-Route außer den Token-Endpoints
  verlangt den Login-Cookie.

### Warum Dev-Modus

Der Dienst führt `reflex run` ohne `--env prod` aus. Der Produktionsmodus verursacht bei
jedem Neuladen ein kurzes Aufblitzen ungestylter Inhalte (React Router 7 mit
`prerender: true` lädt das CSS asynchron). Der Dev-Modus kostet etwas mehr
RAM, nicht minifizierte Bundles und mehr Konsolenwarnungen — vernachlässigbar für einen Heim-
server.

Zwei Konsequenzen:
- `scripts/patch-vite-config.sh` läuft vor jedem Start und patcht die
  generierte `.web/vite.config.js`: `allowedHosts` aus `AIFRED_ALLOWED_HOST`
  und `dedupe` für gemeinsam genutzte Frontend-Bibliotheken (ohne das landet `react-helmet`
  in mehreren Lazy-Chunks und der Browser stürzt ab mit
  *"Identifier 'scrollState' has already been declared"*). Idempotent.
- `/api`, `/_upload` und `/_event` werden **nicht** von Vite weitergeleitet — der Reverse-
  Proxy leitet sie ans Backend (siehe [Auf die Web-UI zugreifen](#auf-die-web-ui-zugreifen)).

### Reflex-Patches

Zwei Patches gegen Reflex-Bugs sind nötig; prüfe sie nach jedem Reflex-Upgrade
erneut:

| Patch | Angewendet von | Warum |
|---|---|---|
| `route.py` — Route-Matching bei `frontend_path` | `scripts/patch-reflex.py` (Installationsskript) | Ohne ihn feuert `on_load` nie und die App bleibt bei „wird initialisiert…“ hängen |
| `utils/exec.py` — `run_granian_backend()`: `reload=False`, `respawn_failed_workers=True`, `respawn_interval=3.5` | manuell | Ohne ihn wird ein Backend-Worker, der durch einen C-Level-Crash stirbt, nie neu gestartet, und AIfred bleibt tot bis zum manuellen Neustart |

Der zweite Patch schaltet außerdem den Backend-Hot-Reload ab — starte den Dienst nach
Code-Änderungen neu.

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
2. Erstellt einen Symlink `~/models/Qwen3-14B-Q8_0.gguf` → Ollama-Blob
3. Führt einen 6-sekündigen Kompatibilitätstest mit llama-server durch
4. Schreibt einen Eintrag in `~/.config/llama-swap/config.yaml`
5. Aktualisiert die Liste `groups.main.members` in der Konfiguration

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
2. Erstellt einen Symlink `~/models/Qwen3-14B-Q8_0.gguf` → HF-Cache-Pfad
3. Führt den Kompatibilitätstest aus und schreibt den YAML-Eintrag
4. Aktualisiert die Liste `groups.main.members` in der Konfiguration

Wenn eine passende `mmproj-*.gguf`-Datei im selben HF-Snapshot vorhanden ist,
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
`/api/*`- und `/_upload/*`-Anfrage liefert einen 404-Fehler – daher bleiben Kamera-Miniaturansichten, das Vigilantia-
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
2. Entfernt Konfigurationseinträge, deren `--model`-Pfad nicht mehr existiert
3. Entfernt veraltete Einträge aus der Kompatibilitäts-Skip-Liste
4. Entfernt verwaiste VRAM-Cache-Einträge
5. Aktualisiert die Liste `groups.main.members`

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
   - Jede angekreuzte Zelle wird zu einem separaten `<base>-vlm-<key>-tts-<engine>`-
     llama-swap-Profil, das der Chat-Path-Resolver automatisch übernimmt
4. Klicke auf **„Kalibrierung starten“**. Die Matrix zeigt pro Zelle drei Zustände an:
   - 🟢 grüner Punkt — kalibriert
   - 🔴 roter Punkt — versucht, aber fehlgeschlagen (mit der Maus darüberfahren, um den Grund zu sehen)
   - leer — noch nie versucht

Was im Hintergrund abläuft:

- **Greedy-Kaskade**: Fülle zuerst die schnellste Rechenklasse, weiche dann auf die
  nächste aus, minimiere die Anzahl aktiver GPUs
- **Stress-Burn-in** bei der ersten TTS/VLM-Nutzung: Eine zweisprachige TTS-Synthese-Schleife im Worst-Case-Szenario
  und eine VLM-Kontext-Füllung zum Vorwärmen messen den Spitzen-VRAM-Verbrauch unter
  Last. Ergebnisse werden in `data/tts_vram_cache.json` /
  `data/vlm_vram_cache.json` zwischengespeichert – nachfolgende Kalibrierungen nutzen die Messwerte wieder
- **Side-Channel-Kapazitätsüberwachung**: Bevor ein `tts-engine + vlm`-
  Kombinationsprofil geschrieben wird, prüft der Kalibrator, ob beide Reserven auf die
  gemeinsam genutzte Side-Channel-GPU passen. Kombinationen, die zur Laufzeit einen OOM auslösen würden, werden
  mit einem roten Punkt abgelehnt
- **Bias-verfolgte binäre Suche**: Wenn `llama-fit-params` durchgehend
  danebenliegt (typisch bei MoE-Modellen), wird der Bias über alle Probes hinweg verfolgt und
  in die mathematische Projektion zurückgeführt, sodass die Suche bereits nach 3–5
  Proben statt über 25 konvergiert
- Die Endergebnisse werden in `data/model_vram_cache.json` und als Profil-
  Einträge in `~/.config/llama-swap/config.yaml` gespeichert

> **Strategie-Referenz (SSOT):** [calibration-strategy.md](../architecture/calibration-strategy.md)

Ohne Kalibrierung funktioniert das Modell trotzdem – es läuft mit dem nativen
Kontext. Wenn dieser den VRAM überschreitet, schlägt die erste Anfrage mit einem OOM-Fehler
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
Kalibrierung (Abschnitt 9) erneut durch, wobei die Zeile **Vigilantia 4B** oder **Vigilantia 8B**
angekreuzt ist – das erzeugt ein `<base>-vlm-<key>`-Profil, und der
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
`unknown`-Ereignis angezeigt.

1. Mach einen Schnappschuss eines Bildes von einer Kamera mit einem deutlich erkennbaren Gesicht
2. Öffne das **Personarium**-Modal
3. Multi-Pose-Assistent: Erfasse eine Frontalaufnahme + 4 Winkel
4. Weise einen Namen + (optional) eine Gruppe zu
5. Die Gesichtsvektoren werden im SQLite-Speicher abgelegt; beim nächsten Watcher-Durchlauf
   werden übereinstimmende Gesichter als `known` klassifiziert

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
  als `known` gilt (Standard 0,6)
- `face_detect.threshold_unsure` — unterhalb von `known`, aber oberhalb dieses Wertes →
  `unsure` (Standard 0,5). Darunter → `unknown`
- `events.retention_days_*` — Aufbewahrungsdauer pro Ereignistyp

### Den Casus-Ereignis-Browser nutzen

Das **Casus**-Modal ist das zentrale Tool zur Ereignisüberprüfung:

- Nach Typ filtern (motion / face_known / face_unsure / face_unknown / vlm_analysis)
- Nach Quelle, Gesichts-ID oder Zeit filtern
- **VLM-Analyse für einzelne Ereignisse**: Klicke auf ein beliebiges Ereignis → „Mit VLM analysieren“
  — führt das konfigurierte VLM auf dem gespeicherten Bild aus
- **VLM-Massenanalyse**: Wähle N Ereignisse aus → Ein Hintergrundprozess führt die
  VLM für jedes einzelne durch, mit Fortschrittsanzeige und Abbruchoption. Eine VRAM-Vorabprüfung bricht den Vorgang sauber ab,
  wenn nicht genügend VRAM-Reserve für den konfigurierten VLM-Stapel vorhanden ist
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
