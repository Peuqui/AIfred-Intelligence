# AIfred-Einrichtungsanleitung

> **English version:** [deployment.md](../../en/guides/deployment.md)

Anleitung zur Einrichtung einer neuen AIfred-Installation mit dem llama.cpp-Backend (llama-swap).

**Zuletzt aktualisiert:** 08.11.2026

> **TL;DR – der schnellste Weg:** `./scripts/install-all.sh` aus einem frischen
> Klon kümmert sich in einem Durchgang um Abhängigkeiten, venv, Playwright, den Bau der
> Oberfläche (Node.js), optionale systemd-Dienste, `.env`, den `bge-m3`-Embedding-Pull,
> die Begleitdienste (TTS-Suite, STT-Suite, SearXNG, GPU-Orc) und einen ersten
> Whitelist-Benutzer. Die folgenden Abschnitte beschreiben
> denselben Ablauf **manuell** für die Fehlersuche und für nicht standardmäßige Konfigurationen
> (Multi-GPU-Systeme, Kameraüberwachung, Koexistenz mit vLLM).

---

## Überblick

AIfred nutzt **llama-swap** als Proxy-Daemon für llama.cpp. llama-swap verwaltet
mehrere Modelle und lädt sie bei Bedarf. Der **autoscan**-Mechanismus erkennt neue
Modelle automatisch und konfiguriert sie, ohne dass manuelle YAML-Bearbeitung nötig ist.

```
User <-> AIfred (Server + Vue-Oberfläche, :8002) <-> llama-swap (:11435) <-> llama-server (per model)
```

Neben AIfred laufen drei eigene Dienste, jeder aus einem eigenen Repository; der Installer richtet sie
ein:

| Dienst | Port | Aufgabe |
|---|---|---|
| [TTS-Suite](https://github.com/Peuqui/TTS-Suite) | 5090 | Sprachausgabe: Engines in Containern, auf anderen Rechnern, in der Cloud ([9a](#9a-sprachausgabe-tts-suite)) |
| [STT-Suite](https://github.com/Peuqui/STT-Suite) | 5080 | Spracherkennung: Whisper und Parakeet, ein CPU-Modell immer, ein GPU-Worker bei Bedarf ([9b](#9b-spracherkennung-stt-suite)) |
| [GPU-Orc](https://github.com/Peuqui/GPU-Orc) | 5070 | räumt die Karten frei, bevor ein Modell lädt: bittet die beiden Suiten und llama-swap, loszulassen ([9c](#9c-karten-freiräumen-gpu-orc)) |

---

## 1. Voraussetzungen

### Hardware
- NVIDIA-GPU mit CUDA-Unterstützung (Compute Capability >= 6.1, Pascal oder neuer)
- Empfohlen: >= 24 GB VRAM für brauchbare Modellgrößen

### Software
- Linux mit systemd (Ubuntu/Debian empfohlen)
- CUDA Toolkit >= 12.0
- Python 3.12+ (`requirements.txt` legt Wheels fest, die unter 3.12 getestet sind)
- Node.js ^20.19 oder >= 22.12 mit npm, zum Bauen der Oberfläche (Vite 8). Ubuntu 24.04 und Debian 12
  bringen Node 18 mit, das ist zu alt
- Git
- polkit (`polkitd` unter Debian/Ubuntu; der Installer richtet es ein): Dein Benutzer startet die Units
  von TTS-Suite und GPU-Orc ohne sudo neu, und AIfred startet die TTS-Suite, wenn sie gebraucht wird

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
venv/bin/pip install -r requirements.txt
# insightface und der GPU-Build von onnxruntime: ohne ihre eigenen Abhängigkeitslisten, zuletzt
venv/bin/pip install --no-deps --force-reinstall -r requirements-nodeps.txt

# Die Oberfläche: frontend/ → frontend/dist (npm ci, npm run build); nach jedem Update von frontend/ erneut
./scripts/build-frontend.sh
```

### Installation ohne Rückfragen

`install-all.sh` stellt fünf Arten von Fragen. Jede lässt sich vorab über
eine Umgebungsvariable beantworten; ist die Variable nicht gesetzt, wird wie
gewohnt gefragt — ohne Terminal gilt die Standardantwort:

| Variable | Frage | Werte |
|---|---|---|
| `AIFRED_INSTALL_OLLAMA` | Ollama installieren (`curl \| sh`) | `y` / `n` (Standard `y`) |
| `AIFRED_INSTALL_SYSTEMD` | systemd-Dienste installieren | `y` / `n` (Standard `y`) |
| `AIFRED_INSTALL_USER` | erster Whitelist-Benutzer | ein Benutzername oder `skip` (Standard: `$USER`) |
| `AIFRED_INSTALL_TTS_SYSTEMD` | TTS-Suite als Systemdienst `tts-suite@<Benutzer>` installieren (sudo, mit Polkit-Regel) | `y` / `n` (Standard `y`; `n`: Benutzerdienst, läuft nur, solange du angemeldet bist) |
| `AIFRED_INSTALL_GPU_ORC_SYSTEMD` | GPU-Orc als Systemdienst `gpu-orc@<Benutzer>` installieren (sudo, mit Polkit-Regel) | `y` / `n` (Standard `y`; `n`: Benutzerdienst, läuft nur, solange du angemeldet bist) |

```bash
AIFRED_INSTALL_OLLAMA=n AIFRED_INSTALL_SYSTEMD=y AIFRED_INSTALL_USER=alice ./scripts/install-all.sh
```

---

## 5. systemd-Dienste einrichten

Für die AIfred-Dienste (chromadb + intelligence + optionale
Bibliothek) nutze das Installationsskript – es ist update-sicher:

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
Dienst wird nur neu gestartet, wenn sich seine eigene Unit geändert hat – erneute
Ausführungen auf einem sauberen System haben keine Auswirkungen.

| Unit | Startet |
|---|---|
| `aifred-chromadb.service` | `docker compose up -d chromadb searxng` — Vector Store + Websuche |
| `aifred-intelligence.service` | AIfreds Server auf Port `8002`: API, Oberfläche, Ordner |
| `aifred-bibliothek.service` | optionale Such- und Verwaltungs-API der AIfred Bibliothek (`127.0.0.1:8005`, für `deploy/bibliothek/`) |

Die Begleitdienste bringen eigene Units mit, eingerichtet von den Skripten ihrer Repositories (die
`install-all.sh` aufruft): `tts-suite@<Benutzer>` und `gpu-orc@<Benutzer>` als System-Units mit
Polkit-Regel oder als Benutzerdienste; die STT-Suite läuft als Docker-Container
(`restart: unless-stopped`).

Das Installationsskript rendert `systemd/aifred-intelligence.service` (und die
chromadb-/Bibliothek-Einheiten) in `/etc/systemd/system/`, ersetzt die
tatsächlichen Benutzer- und Projektpfade, lädt systemd neu und aktiviert die Einheiten. Dies
sind **Dienste auf Systemebene** (`WantedBy=multi-user.target`, laufen als
`User=<du>`) – verwalte sie mit `sudo systemctl`, nicht mit `systemctl --user`.

> **Tipp:** Für `enable`/`disable` und das Bearbeiten von Units ist immer `sudo` erforderlich (sie
> schreiben in `/etc/systemd/system/`). Die Laufzeitbefehle `restart`,
> `stop` und `status` können **ohne** `sudo` ausgeführt werden, wenn du eine PolKit-Regel hinzufügst,
> die es deinem Benutzer erlaubt, diese spezifischen Units zu verwalten – praktisch für die
> häufigen `restart llama-swap` / `restart aifred-intelligence` während der
> Feinabstimmung. Ohne eine solche Regel musst du ihnen `sudo` voranstellen.

Die AIfred-Einheit startet den Server mit dem Python des venv, ein Prozess:

```
ExecStart=<project>/venv/bin/python -m aifred.server
```

Er liefert die API unter `/api`, die gebaute Oberfläche unter `/api/app/` (`/` leitet dorthin), die
Ordner unter `/_upload/` und die Bibliotheken der Sandbox-Seiten unter `/vendor/` aus.
`KillMode=control-group` beendet auch seine Kindprozesse (den Arbeitsprozess der Gesichtserkennung).
Nach einem Update von `frontend/` vor dem Neustart `./scripts/build-frontend.sh` ausführen.

Bearbeite die Units unter `/etc/systemd/system/` nicht von Hand – die Vorlagen in
`systemd/` enthalten die Platzhalter `__USER__` / `__PROJECT_DIR__` / `__DOCKER_BIN__`,
die nur das Installationsskript ausfüllt. Ändere die Vorlage und führe
`sudo ./scripts/install-services.sh` erneut aus. Details: [systemd/README.md](../../../systemd/README.md).

**Maschinenspezifische Einstellungen** überleben eine Neuinstallation nur außerhalb
der Unit: Werte (URL-Präfix, Ports, …) gehören in die `.env` (siehe [Umgebung](#umgebung-env)),
zusätzliche Befehle wie ein `ExecStartPre` in ein Drop-in, angelegt mit
`sudo systemctl edit aifred-intelligence`. Der Installer überschreibt eine geänderte
Unit (mit Sicherung `.pre-aifred-<Zeitstempel>`), außer du gibst `--no-overwrite` mit.

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
| `MESSAGE_HUB_OWNER` | **Pflicht.** AIfred-Konto, dem die Message-Hub-Sessions gehören (Kanäle, Scheduler, Webhook); schreibt `install-all.sh` mit dem ersten Benutzer — ohne den Wert startet AIfred nicht |
| `AIFRED_SERVER_HOST`, `AIFRED_SERVER_PORT` | Wo der Server lauscht (Standard `0.0.0.0`, `8002`) |
| `INJECT_API_TOKEN` | Token für `/api/chat/inject` (siehe [REST API](rest-api.md)) |
| `WEBHOOK_API_TOKEN` | Token für `/api/agent/trigger` |
| `ANNOUNCE_API_TOKEN` | Bearer-Token für `/api/audio/announce` |
| `TTS_CONTROL_API_TOKEN` | Bearer-Token der TTS-Start/Stopp-API (Dienste-Steuerseite) |
| `GPU_RELEASE_API_TOKEN` | Bearer-Token von `/api/gpu/holders` und `/api/gpu/release`: GPU-Orc beendet damit den Arbeitsprozess der Gesichtserkennung ([9c](#9c-karten-freiräumen-gpu-orc)) |
| `LIST_DEFAULT`, `READ_MEMORY_GUARD_BYTES` und weitere Stellschrauben | Siehe [Konfiguration → Umgebungsvariablen und Plugin-Einstellungen](configuration.md#umgebungsvariablen-und-plugin-einstellungen) |
| `AIFRED_SESSION_SECRET` | Signiert die Login-Cookies (optional — sonst wird ein Zufalls-Secret persistiert) |
| `LLAMACPP_URL` | llama-swap-URL (Standard `http://localhost:11435/v1`) |
| `LLAMACPP_CALIBRATION_PORT` | Port des temporären Kalibrier-Servers (Standard `9999`) |
| `BRAVE_API_KEY`, `TAVILY_API_KEY` | Optionale zusätzliche Such-APIs (SearXNG braucht keinen Key) |
| `ANTHROPIC_API_KEY`, `DASHSCOPE_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY` | Cloud-LLM-Provider; `DASHSCOPE_API_KEY` schaltet auch die Cloud-TTS-Engine Qwen-Audio 3 frei |
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

### Wie die Oberfläche den Server erreicht

Die Oberfläche liegt auf demselben Ursprung wie die API: Sie spricht die API relativ zu ihrer eigenen
Adresse an (`../` von `/api/app/` aus) und funktioniert deshalb unter `http://<host>:8002/` ebenso wie
hinter einem Proxy unter einem Unterpfad. Die Live-Aktualisierungen kommen als Server-Sent Events (kein
WebSocket). Jede `/api`-Route außer den Token-Endpunkten und dem Login verlangt das Login-Cookie, ebenso
die Ordner unter `/_upload/` (außer den Freigabe-Links unter `/_upload/html_preview`).

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

# GPU-Orc answers (AIfred asks it before every model load)
curl -s http://127.0.0.1:5070/health

# Start AIfred
sudo systemctl start aifred-intelligence
```

Typische Ausgabe des Autoscans (ein neues Ollama-Modell; `…` steht für
rechnerabhängige Werte):
```
=== llama-swap Autoscan ===

GPU hardware: …

Scanning Ollama models...
  ~ Skip:    nomic-embed-text-v2-moe:latest (embedding model)
  + Symlink: Qwen3-14B-Q8_0.gguf → sha256-…...
  = Exists:  Qwen3-8B-Q4_K_M.gguf
  2 Ollama models found, 1 new symlinks created

Scanning HuggingFace cache...
  No HuggingFace cache found or empty.

Cleaning up...
  … symlink(s) checked — all targets valid
  … config entry/entries checked — all model files present
Maintaining -visiond describer profiles...
  visiond profiles up to date

Scanning ~/models/ for GGUFs...
  Found … GGUFs, 1 new

Testing new models for llama-server compatibility...
  ✓ Qwen3-14B-Q8_0 (OK)

Calibrating new models (llama-fit-params)...
    GPU: single (model … MB = …% of largest GPU … MB)
  Qwen3-14B-Q8_0 (… MB, native context: 40,960):
    ✓ KV=f16, context=40,960 (min free: … MB)

Updating llama-swap-config.yaml...
  + Added: Qwen3-14B-Q8_0 (context: 40,960)
Updating VRAM cache...
  + Added: Qwen3-14B-Q8_0

Scanning for vLLM checkpoint directories...
  no vLLM checkpoint dirs found

Groups updated: main → [Qwen3-14B-Q8_0, Qwen3-8B-Q4_K_M]
  … VRAM cache entry/entries checked — all match active config

Done. 1 added, 1 VRAM cache entries added.
```

Der `llama-fit-params`-Schritt legt den Kontext fest, der in die YAML kommt: den
größten Kontext bis zum nativen, bei dem auf jeder GPU noch 1024 MiB frei
bleiben, der Reihe nach mit KV f16 → q8_0 → q4_0 (und mit kleinerem `-ngl`,
falls selbst 32.768 Token bei voller Auslagerung auf die GPU nicht passen).
Passt der native Kontext nicht, zeigt die `+ Added:`-Zeile den reduzierten Wert,
dazu `KV: …` / `ngl: …`, wenn diese abgesenkt wurden. Die `GPU:`-Zeile steht
vorn, weil die GPU-Aufteilung vor der Suche festgelegt wird; bei nur einer GPU
fehlt sie.

### Auf die Web-UI zugreifen

Ein Prozess liefert alles aus: **`http://<host>:8002/`** öffnen (leitet zur Oberfläche unter `/api/app/`
weiter). Dafür braucht es keinen Reverse-Proxy.

Für eine eigene Domain, TLS oder einen Unterpfad kommt ein Reverse-Proxy davor. Er leitet den Unterpfad
auf die Wurzel des Servers weiter, `/_upload/` und `/vendor/` unverändert: Links auf Dateien in Antworten
und die Bibliotheken der Sandbox-Seiten beginnen an der Wurzel. Minimale nginx-Skizze (generisch — eigenen
Host, Ports und bei Bedarf TLS/Auth einsetzen):

```nginx
server {
    listen 80;
    server_name your-host.example;   # oder eine LAN-IP
    client_max_body_size 2G;         # Uploads (Audio bis AUDIO_UPLOAD_MAX_MB, Standard 2048 MB)

    # API, Oberfläche, Server-Sent Events → Wurzel des Servers
    location /aifred/ {
        proxy_pass http://127.0.0.1:8002/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Dateien und Bibliotheken der Sandbox-Seiten, ab der Wurzel verlinkt
    location ~ ^/(_upload|vendor)/ {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
```

Mit dem Proxy **`http://your-host.example/aifred/`** öffnen. Die Ereignisströme senden den Kopf gegen
Pufferung (`X-Accel-Buffering: no`) und alle 15 s ein Keep-alive, nginx braucht für sie keine weiteren
Einstellungen.

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
2. Entfernt Konfigurationseinträge, deren Modelldatei nicht mehr existiert (die
   `--model`-Datei bzw. der Checkpoint oder ein in `--speculative-config`
   genanntes Draft-Modell)
3. Entfernt veraltete Einträge aus der Kompatibilitäts-Skip-Liste
4. Entfernt Betriebspunkt-Profile in `data/operating_points/`, deren
   Modelldatei fehlt, sowie `-vlm-<key>`-Varianten, deren
   `-visiond`-Describer-Profil fehlt
5. Entfernt verwaiste VRAM-Cache-Einträge
6. Aktualisiert die Liste `groups.main.members`

Beispiel für die Bereinigungsausgabe (`…` steht für rechnerabhängige Pfade):
```
Cleaning up...
  ✗ Qwen3-8B-Q8_0.gguf → …/blobs/sha256-… (target missing, removed)
  ✗ Qwen3-8B-Q8_0 — model file missing: …/models/Qwen3-8B-Q8_0.gguf
Maintaining -visiond describer profiles...
  visiond profiles up to date
  → 2 item(s) cleaned up

…

Groups updated: main → [Qwen3-14B-Q8_0]
  ✗ VRAM cache: Qwen3-8B-Q8_0 (no longer in config, removed)
  1 stale VRAM cache entry/entries removed

Done. 1 removed.
```

Keine manuelle YAML-Bearbeitung erforderlich.

---

## 9. VRAM-Kalibrierung

Der Autoscan trägt neue Modelle mit einer **ersten Anpassung per `llama-fit-params`**
ein (siehe [Abschnitt 7](#7-starten-und-überprüfen)): dem größten Kontext bis
zum nativen aus den GGUF-Metadaten, den die Projektion mit 1024 MiB Reserve pro
GPU im VRAM unterbringt. Dieser Wert ist eine Projektion, keine Messung – gerade
bei MoE-Modellen liegt `llama-fit-params` oft daneben. Die Kalibrierung lädt das
Modell tatsächlich und ermittelt das echte Maximum.

So führst du die Kalibrierung in der AIfred-Benutzeroberfläche durch:

1. Wähle das neue Modell in AIfred aus
2. Klicke neben der Modellauswahl auf **„Kalibrieren“**
3. Wähle die gewünschten Varianten über den **2D-Matrix-Picker** aus:
   - Zeilen = VLM-Auswahl (Kein VLM / Vigilantia 4B / Vigilantia 8B)
   - Spalten = Kein TTS plus jede lokale GPU-TTS-Engine, die die TTS-Suite als installiert anbietet (Qwen3-TTS, XTTS)
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
  Last. Das VLM-Ergebnis wird in
  `data/vlm_vram_cache.json` zwischengespeichert; die TTS-Reserve nennt die TTS-Suite (`needed_mib`) – nachfolgende Kalibrierungen nutzen die Messwerte wieder
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

Ohne Kalibrierung funktioniert das Modell trotzdem – es läuft mit dem Kontext aus
der `llama-fit-params`-Anpassung des Autoscans. Liegt kein `llama-fit-params`
neben `llama-server`, setzt der Autoscan stattdessen sichere Defaults (Kontext
höchstens 32.768, q4_0-KV). Überschätzt die Projektion, was passt, bricht das
Modell beim Laden oder bei der ersten langen Anfrage mit einem OOM-Fehler ab –
dann kalibrieren (oder `-c` von Hand senken, siehe
[Fehlerbehebung](#oom-absturz--kontext-zu-groß)).

---

## 9a. Sprachausgabe (TTS-Suite)

Die Sprachausgabe kommt aus der **TTS-Suite**, einem eigenen Dienst
([Peuqui/TTS-Suite](https://github.com/Peuqui/TTS-Suite)). Engines, ihre Docker-Container, die
Kartenwahl und weitere Rechner mit Engines (Hosts) gehören ihr; AIfred schickt nur Text und seine
Liste (Semantik in [Konfiguration → Sprachausgabe](configuration.md#sprachausgabe-tts) und
[TTS-Suite](../architecture/tts-suite.md)).

- **Installation:** `install-all.sh` (Schritt 2d) klont das Repository neben AIfred, führt dessen
  `deploy/install.sh` aus (virtuelle Umgebung, Befehl, Token, erste Konfiguration mit den Stimmen des
  Klons und der Aussprache „AIfred → Alfred“) und fragt, ob die Systemeinheit `tts-suite@<Benutzer>`
  mit der Polkit-Regel installiert werden soll (ein sudo-Schritt, `deploy/switch-to-system-unit.sh`;
  nur damit kann AIfred den Dienst bei Bedarf selbst starten). Danach prüft es `/health` auf Port
  5090. Ohne Docker mit Compose starten die lokalen Engines nicht; Cloud-Engines und die
  Browser-Stimme gehen trotzdem. Von Hand: `deploy/install.sh` und `sudo deploy/switch-to-system-unit.sh`.
- AIfred startet die Einheit, wenn sie nicht läuft (`ensure_ready`, braucht die Polkit-Regel). Ohne
  den Dienst läuft AIfred, die Sprachausgabe scheitert aber laut (die Browser-Stimme spricht, wenn
  ihr Eintrag eingeschaltet ist).
- **Hosts** (ein zweiter Rechner mit GPU, der das TTS übernimmt): Sie werden in der eigenen
  Weboberfläche des Dienstes bzw. in `~/.config/tts-suite/config.json` konfiguriert (Name,
  Adresse, SSH), nicht in AIfred.
- **Stimmen:** Die Referenzstimmen liegen im Stimmenordner des Dienstes (`voices_dir` seiner
  Konfiguration); eine später hinzugefügte Stimme wird nach einem Neustart des Containers gesehen.


## 9b. Spracherkennung (STT-Suite)

Die Spracherkennung kommt aus der **STT-Suite** ([Peuqui/STT-Suite](https://github.com/Peuqui/STT-Suite)),
einem Container mit Whisper und Parakeet auf Port 5080. `install-all.sh` (Schritt 2e) klont das
Repository neben AIfred (oder `STT_SUITE_DIR` aus der `.env`) und startet es mit `docker compose`.
**Ohne NVIDIA-Karte** (`nvidia-smi` liefert keine) nimmt der Installer zusätzlich
`docker-compose.cpu.yml`: Die Basisdatei reserviert eine GPU, was Docker dort ablehnt. AIfred macht
dasselbe, wenn es den Container selbst startet (`STTClient.start_container`).

- **Was AIfred einstellt:** nichts am Gerät. AIfred fragt immer mit `device=auto`: Die STT-Suite
  nimmt eine Karte nur, wenn dort Platz ist, sonst die CPU. Lange Aufnahmen (über
  `WHISPER_GPU_MIN_FILE_MB`, 10 MB) schickt AIfred auf die CPU. Wer die Karten nie anfassen lassen
  will, stellt das in der Automatik-Liste der STT-Suite ein. Der STT-Tab im Agent-Editor zeigt nur,
  ob der Dienst erreichbar ist.
- **Was die STT-Suite einstellt** (ihre Oberfläche unter `/ui/`, am Proxy `/stt-suite/ui/`): Engine,
  Modell und Genauigkeit je Gerät, die Reihenfolge der Automatik, der Speicherpuffer und die
  Messung des Speicherbedarfs.
- **GPU freigeben:** Bevor ein Modell auf seine Karte lädt, beendet GPU-Orc den GPU-Worker (die
  CPU-Modelle bleiben). Eine Transkription, die mit `device=auto` noch läuft, wird dann mit derselben
  Datei auf der CPU noch einmal gemacht; verloren geht nichts ([9c](#9c-karten-freiräumen-gpu-orc)).

## 9c. Karten freiräumen (GPU-Orc)

Bevor ein Modell lädt, kann etwas anderes seine Karten belegen: der GPU-Worker der STT-Suite, eine
lokale TTS-Engine, die dauerhaften Hilfsmodelle von llama-swap (Describer, Embedder).
[GPU-Orc](https://github.com/Peuqui/GPU-Orc) ist die eine Stelle je Rechner, die weiß, wie man sie
freiräumt. AIfred fragt ihn vor jedem Kaltstart eines Modells über llama-swap, vor dem Wechsel auf ein
TTS-kalibriertes Profil und vor einer Kalibrierung; die Platzprüfung des Describers liest bei ihm, wer
welche Karte belegt. GPU-Orc bittet die Dienste über deren eigene Schnittstellen loszulassen
(`POST /gpu/release` der Suiten, das Entladen von llama-swap) und prüft das Ergebnis nach (Status und
Speicher je Karte); er beendet nie selbst einen Prozess.

- **Installation:** `install-all.sh` (Schritt 2g) klont das Repository neben AIfred (oder
  `GPU_ORC_DIR` aus `.env`), führt dessen `deploy/install.sh` aus (eigene virtuelle Umgebung, Befehl
  `gpu-orc`, erste Konfiguration) und fragt, ob die System-Unit `gpu-orc@<Benutzer>` mit Polkit-Regel
  eingerichtet werden soll (ein sudo-Schritt, `deploy/switch-to-system-unit.sh`: dann startet sie beim
  Hochfahren ohne Anmeldung, und ein Neustart braucht kein Passwort). Sonst läuft GPU-Orc als
  Benutzerdienst. Danach prüft der Installer `/health` auf Port 5070. Ein erneuter Lauf holt das
  Repository neu und startet den Dienst neu.
- **Konfiguration:** `~/.config/gpu-orc/config.yaml`, bei der ersten Installation aus dem Beispiel
  geschrieben. Sie nennt die STT-Suite (`127.0.0.1:5080`), die TTS-Suite (`127.0.0.1:5090`) und
  llama-swap (`127.0.0.1:11435`, mit dessen `~/.config/llama-swap/config.yaml`). Je Suite: wie lange
  gewartet wird, solange sie beschäftigt ist (`busy_wait_s`), und ob danach erzwungen wird
  (`force_after_wait`):
  - STT 30 s: Ein Diktat ist auf der GPU in Sekunden fertig; eine lange Aufnahme mit `device=auto`
    wird danach mit derselben Datei auf der CPU noch einmal transkribiert.
  - TTS 5 s: Eine Vorlesung endet dann mit `reading_aborted`, eine Engine, die noch startet, wird
    abgeschossen (`docker kill`).
  - Eine Anfrage endet spätestens nach `free_timeout_s` (180 s). Nach einer Änderung:
    `systemctl restart gpu-orc@<Benutzer>` (oder `systemctl --user restart gpu-orc`).
- **Ampel:** Solange ein großes Modell lädt, sehen seine Karten leerer aus, als sie gleich sind. Alles,
  was etwas auf eine Karte legen will, fragt deshalb vorher `GET /loading`: AIfreds Describer, die
  TTS-Suite (`gpu_orc_url` in ihrer Konfiguration) und die STT-Suite (`STT_GPU_ORC_URL`, im Container
  mit `STT_GPU_ORC_DIR` und `STT_GPU_ORC_TOKEN_FILE`). Lädt etwas oder antwortet GPU-Orc nicht, geht
  TTS zum nächsten Listeneintrag und STT zur CPU, und ein Describer kommt erst danach dazu.
- **AIfreds Seite:** `GPU_ORC_URL` in `.env` (Standard `http://127.0.0.1:5070`) und
  `GPU_ORC_MODEL_SERVER` (der Name des Modellservers unter den Verbrauchern in GPU-Orcs Konfiguration,
  Standard `llama-swap`). Sein HTTP-Timeout nimmt AIfred aus GPU-Orcs `/health`.
- **AIfred als Verbraucher:** Seine Gesichtserkennung läuft in einem eigenen Prozess und gibt ihre
  Karte über die Schnittstelle der Suiten zurück (`/api/gpu/holders`, `/api/gpu/release`, Element
  `face-worker`, freigegeben, sobald nvidia-smi den Prozess nicht mehr führt). `GPU_RELEASE_API_TOKEN` in
  die `.env`, dasselbe Token in eine Datei nur für den Dienstnutzer, und in GPU-Orcs Konfiguration (ab
  0.8.0): `{name: aifred, type: gpu-release, url: http://127.0.0.1:8002/api, token_file: <diese Datei>, busy_wait_s: 5}`.
  Bei roter Ampel startet die Gesichtserkennung auf der CPU und zieht bei der ersten Erkennung nach Grün
  zurück auf ihre Karte.
- **Ohne GPU-Orc** läuft und lädt AIfred trotzdem, aber jedes Laden meldet laut im Log, dass die
  Karten nicht freigeräumt werden konnten, und die Platzprüfung des Describers findet keinen Platz.
  **Ohne NVIDIA-Karte** läuft GPU-Orc, hat aber nichts freizuräumen.
- **Prüfen:** `curl -s http://127.0.0.1:5070/status` zeigt je Karte, wer was belegt. Eine Vorschau
  ohne Wirkung: `curl -s -X POST http://127.0.0.1:5070/free -H 'Content-Type: application/json' -d
  '{"model":"<Modell>","reason":"Test","dry_run":true}'`.
- **Aufrufer auf anderen Rechnern** (etwa ein Agent auf einem anderen Rechner, der hier Modelle lädt):
  GPU-Orc lauscht dann auf allen Adressen und verlangt ein Token (`listen.host` und `token_file` in
  seiner Konfiguration; Loopback bleibt ohne Token). Einzelheiten:
  [GPU-Orc-Schnittstelle](https://github.com/Peuqui/GPU-Orc/blob/main/docs/interface.de.md).

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

### Jedes Laden meldet „GPU-Orc could not clear the cards“

GPU-Orc antwortet nicht. Prüfen und starten:

```bash
curl -s http://127.0.0.1:5070/health
systemctl status gpu-orc@$USER          # System-Unit (oder: systemctl --user status gpu-orc)
journalctl -u gpu-orc@$USER -n 50       # (oder: journalctl --user -u gpu-orc -n 50)
```

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
