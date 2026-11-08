# AIfred Deployment Guide

> **Deutsche Version:** [deployment.md](../../de/guides/deployment.md)

Setup guide for a fresh AIfred installation with the llama.cpp backend (llama-swap).

**Last updated:** 2026-11-08

> **TL;DR — fastest path:** `./scripts/install-all.sh` from a fresh
> clone handles dependencies, venv, Playwright, the interface build
> (Node.js), optional systemd services, `.env`, the `bge-m3` embedding pull,
> the companion services (TTS-Suite, STT-Suite, SearXNG, GPU-Orc) and a
> first whitelist user in one go. The sections below describe
> the same flow **manually** for debugging and for non-standard setups
> (multi-GPU rigs, camera surveillance, vLLM coexistence).

---

## Overview

AIfred uses **llama-swap** as a proxy daemon for llama.cpp. llama-swap manages
multiple models and loads them on demand. The **autoscan** mechanism detects new
models automatically and configures them without any manual YAML editing.

```
User <-> AIfred (server + Vue interface, :8002) <-> llama-swap (:11435) <-> llama-server (per model)
```

Next to AIfred run three services of their own, each from its own repository; the installer sets
them up:

| Service | Port | Job |
|---|---|---|
| [TTS-Suite](https://github.com/Peuqui/TTS-Suite) | 5090 | speech output: engines in containers, on other hosts, in the cloud ([9a](#9a-speech-output-tts-suite)) |
| [STT-Suite](https://github.com/Peuqui/STT-Suite) | 5080 | speech recognition: Whisper and Parakeet, a CPU model always, a GPU worker on demand ([9b](#9b-speech-input-stt-suite)) |
| [GPU-Orc](https://github.com/Peuqui/GPU-Orc) | 5070 | clears the cards before a model loads: asks the two suites and llama-swap to let go ([9c](#9c-clearing-the-cards-gpu-orc)) |

---

## 1. Prerequisites

### Hardware
- NVIDIA GPU with CUDA support (Compute Capability >= 6.1, Pascal or newer)
- Recommended: >= 24 GB VRAM for useful model sizes

### Software
- Linux with systemd (Ubuntu/Debian recommended)
- CUDA Toolkit >= 12.0
- Python 3.12+ (`requirements.txt` pins wheels tested on 3.12)
- Node.js ^20.19 or >= 22.12 with npm, to build the interface (Vite 8). Ubuntu 24.04 and Debian 12
  ship Node 18, which is too old
- Git
- polkit (`polkitd` on Debian/Ubuntu; the installer adds it): your user restarts the units of the
  TTS-Suite and GPU-Orc without sudo, and AIfred starts the TTS-Suite when it is needed

---

## 2. Build llama.cpp

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build -j$(nproc)

# Verify the binary exists
ls ~/llama.cpp/build/bin/llama-server
```

> **Note:** The autoscan expects the binary at `~/llama.cpp/build/bin/llama-server`
> by default. If it lives elsewhere, add one existing YAML entry with the correct
> path — the autoscan reads the binary path from existing config entries.

---

## 3. Install llama-swap

```bash
# Download the binary from GitHub Releases into ~/bin/
# https://github.com/mostlygeek/llama-swap/releases
mkdir -p ~/bin
wget -O ~/bin/llama-swap https://github.com/mostlygeek/llama-swap/releases/latest/download/llama-swap-linux-amd64
chmod +x ~/bin/llama-swap

# Create the config directory — the autoscan creates the config file itself
mkdir -p ~/.config/llama-swap
```

> **Note:** The autoscan creates `config.yaml` from scratch when models are found.
> An empty stub is only needed if you start llama-swap before downloading any models.

---

## 4. Set up AIfred

```bash
git clone https://github.com/Peuqui/AIfred-Intelligence ~/Projekte/AIfred-Intelligence
cd ~/Projekte/AIfred-Intelligence

python3 -m venv venv
venv/bin/pip install -r requirements.txt
# insightface and the GPU build of onnxruntime: without their own dependency lists, last
venv/bin/pip install --no-deps --force-reinstall -r requirements-nodeps.txt

# The interface: frontend/ → frontend/dist (npm ci, npm run build); again after every update of frontend/
./scripts/build-frontend.sh
```

### Unattended install

`install-all.sh` asks five kinds of questions. Each can be answered in
advance with an environment variable; a question whose variable is not set
is asked as usual, or takes its default when there is no terminal:

| Variable | Question | Values |
|---|---|---|
| `AIFRED_INSTALL_OLLAMA` | install Ollama (`curl \| sh`) | `y` / `n` (default `y`) |
| `AIFRED_INSTALL_SYSTEMD` | install the systemd services | `y` / `n` (default `y`) |
| `AIFRED_INSTALL_USER` | first whitelist user | a username, or `skip` (default: `$USER`) |
| `AIFRED_INSTALL_TTS_SYSTEMD` | install the TTS-Suite as system service `tts-suite@<user>` (sudo, with the polkit rule) | `y` / `n` (default `y`; `n`: user service, runs only while you are logged in) |
| `AIFRED_INSTALL_GPU_ORC_SYSTEMD` | install GPU-Orc as system service `gpu-orc@<user>` (sudo, with the polkit rule) | `y` / `n` (default `y`; `n`: user service, runs only while you are logged in) |

```bash
AIFRED_INSTALL_OLLAMA=n AIFRED_INSTALL_SYSTEMD=y AIFRED_INSTALL_USER=alice ./scripts/install-all.sh
```

---

## 5. Set up systemd services

For the AIfred-side services (chromadb + intelligence + optional
Bibliothek) use the installer script — it's update-safe:

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

The script reports `= Unchanged`, `♻️ Updated`, `✅ Newly installed` or
`🛡 Kept` per file. `daemon-reload` only fires when a unit changed, and a
service is only restarted when its own unit changed — re-runs on
a clean system are no-ops.

| Unit | Runs |
|---|---|
| `aifred-chromadb.service` | `docker compose up -d chromadb searxng` — vector store + web search |
| `aifred-intelligence.service` | AIfred's server on port `8002`: API, interface, folders |
| `aifred-bibliothek.service` | optional search & admin API of the AIfred Bibliothek (`127.0.0.1:8005`, for `deploy/bibliothek/`) |

The companion services bring their own units, installed by the scripts of their repositories (which
`install-all.sh` runs): `tts-suite@<user>` and `gpu-orc@<user>` as system units with a polkit rule, or
as user services; the STT-Suite runs as a Docker container (`restart: unless-stopped`).

The installer renders `systemd/aifred-intelligence.service` (and the
chromadb / Bibliothek units) into `/etc/systemd/system/`, substitutes the
real user + project paths, reloads systemd and enables the units. These
are **system-level services** (`WantedBy=multi-user.target`, running as
`User=<you>`) — manage them with `sudo systemctl`, not `systemctl --user`.

> **Tip:** `enable`/`disable` and editing units always need `sudo` (they
> write to `/etc/systemd/system/`). The runtime operations `restart`,
> `stop` and `status` can be run **without** `sudo` if you add a PolKit rule
> allowing your user to manage these specific units — handy for the
> frequent `restart llama-swap` / `restart aifred-intelligence` during
> tuning. Without such a rule, prefix them with `sudo`.

The AIfred unit starts the server with the venv Python, one process:

```
ExecStart=<project>/venv/bin/python -m aifred.server
```

It serves the API under `/api`, the built interface under `/api/app/` (`/` redirects there), the folders
under `/_upload/` and the libraries of sandbox pages under `/vendor/`. `KillMode=control-group` also ends
its child processes (the face worker). After an update of `frontend/`, run `./scripts/build-frontend.sh`
before the restart.

Don't edit the units under `/etc/systemd/system/` by hand — the templates in
`systemd/` contain `__USER__` / `__PROJECT_DIR__` / `__DOCKER_BIN__`
placeholders that only the installer fills in. Change the template and re-run
`sudo ./scripts/install-services.sh`. Details: [systemd/README.md](../../../systemd/README.md).

**Machine-specific settings** survive re-installs only outside the unit:
values (URL prefix, ports, …) go into `.env` (see [Environment](#environment-env)),
extra commands such as an `ExecStartPre` into a drop-in created with
`sudo systemctl edit aifred-intelligence`. The installer overwrites a modified
unit (it keeps a `.pre-aifred-<timestamp>` backup) unless you pass
`--no-overwrite`.

### llama-swap service (with autoscan)

llama-swap is **not** part of `install-services.sh` — it is a separate
system-level unit you create once under `/etc/systemd/system/`. The
binary lives in `~/bin/llama-swap` (from Section 3):

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

Adjust the two `$HOME/Projekte/AIfred-Intelligence` paths if you cloned the
repo elsewhere.

### llama-swap restart helper

`scripts/llama-swap-restart` is the maintenance command after downloading a
model or editing the llama-swap YAML. `install-services.sh` links it to
`~/bin/llama-swap-restart`. It goes beyond `systemctl restart`:

1. Stops `llama-swap.service` and waits until it is really inactive
2. Kills leftover `llama-server` processes (SIGTERM, then SIGKILL)
3. Waits until the GPU driver has released the VRAM
4. Deletes orphaned lookup caches (`~/.cache/llama_lookup_*.bin`) whose model
   is no longer in `config.yaml`
5. Runs `llama-swap-build-config` (spec-decoding flags, TTS/vision profiles)
6. Starts llama-swap (autoscan runs as `ExecStartPre`) and waits for `listening`

```bash
hf download <repo> --local-dir ~/models/<name>
llama-swap-restart    # autoscan picks up the new model
```

---

## 5a. Configuration and access

### Environment (`.env`)

Secrets and machine-specific values go into `.env` in the project root
(gitignored; template: `.env.example`). The service loads it via
`EnvironmentFile=`; most keys can also be set in the UI (settings, Plugin
Manager), which writes them back to `.env`.

| Variable | Purpose |
|---|---|
| `MESSAGE_HUB_OWNER` | **Required.** AIfred account that owns Message-Hub sessions (channels, scheduler, webhook); written by `install-all.sh` with the first user — AIfred refuses to start without it |
| `AIFRED_SERVER_HOST`, `AIFRED_SERVER_PORT` | Where the server listens (default `0.0.0.0`, `8002`) |
| `INJECT_API_TOKEN` | Token for `/api/chat/inject` (see [REST API](rest-api.md)) |
| `WEBHOOK_API_TOKEN` | Token for `/api/agent/trigger` |
| `ANNOUNCE_API_TOKEN` | Bearer token for `/api/audio/announce` |
| `TTS_CONTROL_API_TOKEN` | Bearer token of the TTS start/stop API (service control page) |
| `GPU_RELEASE_API_TOKEN` | Bearer token of `/api/gpu/holders` and `/api/gpu/release`: GPU-Orc ends the face worker with it ([9c](#9c-clearing-the-cards-gpu-orc)) |
| `LIST_DEFAULT`, `READ_MEMORY_GUARD_BYTES` and further tunables | See [Configuration → Environment variables and plugin settings](configuration.md#environment-variables-and-plugin-settings) |
| `AIFRED_SESSION_SECRET` | Signs the login cookies (optional — a random secret is persisted otherwise) |
| `LLAMACPP_URL` | llama-swap URL (default `http://localhost:11435/v1`) |
| `LLAMACPP_CALIBRATION_PORT` | Port of the temporary calibration server (default `9999`) |
| `BRAVE_API_KEY`, `TAVILY_API_KEY` | Optional extra search APIs (SearXNG needs no key) |
| `ANTHROPIC_API_KEY`, `DASHSCOPE_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY` | Cloud LLM providers; `DASHSCOPE_API_KEY` also enables the cloud TTS engine Qwen-Audio 3 |
| `DEEPL_API_KEY` | Translator plugin |
| `TELEGRAM_*`, `DISCORD_*`, `EMAIL_*` | Channel plugins — see the [Telegram](telegram-setup.md) / [Discord](discord-setup.md) guides |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Suite plugin ([OAuth](plugins/oauth.md)) |

`docker/.env` holds `SEARXNG_SECRET`, generated once by `install-all.sh`.
Compose refuses to start without it.

### User management

Login is required. Accounts and the registration whitelist are managed with
the admin CLI:

```bash
./aifred-admin users                          # whitelist (who may register)
./aifred-admin add <username>                 # add to whitelist
./aifred-admin remove <username>              # remove from whitelist
./aifred-admin accounts                       # registered accounts
./aifred-admin create <username> [password]   # account + whitelist in one step
./aifred-admin delete <username> [--sessions] # delete account (optionally its sessions)
```

Workflow: add a name to the whitelist → the user registers in the web UI with
username + password → logs in from any device.

### Polkit rule (restart without sudo)

AIfred restarts services itself: the restart button (`aifred-intelligence`),
calibration (`llama-swap`) and the Ollama restart endpoint (`ollama`). For that
the service user needs a Polkit rule:

```bash
sed 's/YOUR_USER/'"$USER"'/' scripts/polkit/10-aifred.rules \
  | sudo tee /etc/polkit-1/rules.d/10-aifred.rules > /dev/null
sudo chmod 644 /etc/polkit-1/rules.d/10-aifred.rules
```

The rule grants exactly these three units to exactly that user.

### How the interface reaches the server

The interface lives on the same origin as the API: it addresses the API relative to its own address
(`../` from `/api/app/`), so it works on `http://<host>:8002/` as well as behind a proxy under a
sub-path. The live updates come as server-sent events (no WebSocket). Every `/api` route except the
token endpoints and the login requires the login cookie, and so do the folders under `/_upload/` (except the share
links under `/_upload/html_preview`).

---

## 6. Adding models

The autoscan detects models from three sources automatically. After adding a model,
restart llama-swap and it will be configured and ready.

```bash
sudo systemctl restart llama-swap
```

### Option A: Ollama

```bash
ollama pull qwen3:14b
sudo systemctl restart llama-swap
```

The autoscan will:
1. Read the Ollama manifest to find the GGUF blob
2. Create a symlink `~/models/Qwen3-14B-Q8_0.gguf` → Ollama blob
3. Run a 6-second compatibility test with llama-server
4. Write an entry to `~/.config/llama-swap/config.yaml`
5. Update the `groups.main.members` list in the config

> **Limitation:** Vision-Language (VL) models pulled via Ollama (e.g. `qwen3-vl`)
> are not compatible with llama-server as a **vision** model. Ollama's GGUF
> blobs omit the MRoPE metadata key that llama.cpp requires, and the
> llama.cpp `--mmproj` path is currently unreliable for Qwen3-VL anyway.
> The autoscan detects this automatically and adds the model to the skip
> list with a hint. **Vision inference runs on a dedicated Ollama VLM
> service** (`ollama-vlm.service`) — see Section 10. llama-swap only ever
> serves such models as plain text LLMs.

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

The autoscan will:
1. Scan `~/.cache/huggingface/hub/` for GGUFs in the active snapshot
2. Create a symlink `~/models/Qwen3-14B-Q8_0.gguf` → HF cache path
3. Run the compatibility test and write the YAML entry
4. Update the `groups.main.members` list in the config

When a matching `mmproj-*.gguf` file is present in the same HF snapshot, the
YAML entry can include `--mmproj` automatically. Note, however, that the
llama-server vision path is unreliable for current Qwen3-VL builds — the
supported vision path is the dedicated Ollama VLM service (see Section 10).

### Option C: Manual GGUF

```bash
# Drop the file directly into ~/models/
cp /path/to/Model.gguf ~/models/

# Or create a symlink
ln -s /path/to/Model.gguf ~/models/Model.gguf

sudo systemctl restart llama-swap
```

### Why ~/models/?

This directory acts as a **unified namespace** for all model sources:
- Ollama blobs have SHA256 hash names (`sha256-6335adf...`) — unusable directly
  in the YAML config
- HuggingFace cache paths are long and nested
  (`~/.cache/huggingface/hub/models--Qwen--Qwen3-14B-GGUF/snapshots/{hash}/...`)
- Manual GGUFs need a defined home

The autoscan always scans `~/models/` and writes `~/models/Name.gguf` into the
YAML. All three sources flow through this namespace via symlinks.

---

## 7. Start and verify

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

Typical autoscan output (one new Ollama model; `…` stands for machine-specific
values):
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

The `llama-fit-params` step picks the context written to the YAML: the
largest context up to the native one that still leaves 1024 MiB free on every
GPU, trying KV f16 → q8_0 → q4_0 (and a lower `-ngl` if even 32,768 tokens do
not fit at full offload). If the native context does not fit, the `+ Added:`
line shows the reduced value, plus `KV: …` / `ngl: …` when those were lowered.
The `GPU:` line comes first because the GPU split is decided before the fit
search starts; with a single GPU it is omitted.

### Access the web UI

One process serves everything: open **`http://<host>:8002/`** (it redirects to the interface under
`/api/app/`). No reverse proxy is needed for that.

For your own domain, TLS or a sub-path, put a reverse proxy in front. It forwards the sub-path to the
server's root, and `/_upload/` and `/vendor/` as they are: links to files in answers and the libraries
of sandbox pages start at the root. Minimal nginx sketch (generic — substitute your own host, ports and,
if desired, TLS/auth):

```nginx
server {
    listen 80;
    server_name your-host.example;   # or a LAN IP
    client_max_body_size 2G;         # uploads (audio up to AUDIO_UPLOAD_MAX_MB, default 2048 MB)

    # API, interface, server-sent events → the server's root
    location /aifred/ {
        proxy_pass http://127.0.0.1:8002/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Files and the libraries of sandbox pages, linked from the root
    location ~ ^/(_upload|vendor)/ {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
```

With the proxy in place, open **`http://your-host.example/aifred/`**. The event streams send no buffering
header (`X-Accel-Buffering: no`) and a keep-alive every 15 s, so nginx needs no extra settings for them.

---

## 8. Removing models

When a model is deleted (via `ollama rm`, removing the GGUF file, or clearing
the HuggingFace cache), the autoscan cleans up automatically on the next
llama-swap restart:

```bash
ollama rm qwen3:8b
sudo systemctl restart llama-swap
```

The autoscan will:
1. Remove broken symlinks in `~/models/`
2. Remove config entries whose model file no longer exists (the `--model`
   file or checkpoint, or a draft model named in `--speculative-config`)
3. Remove stale entries from the compatibility skip list
4. Remove operating-point profiles in `data/operating_points/` whose model
   file is gone, and `-vlm-<key>` variants whose `-visiond` describer profile
   is gone
5. Remove orphaned VRAM cache entries
6. Update the `groups.main.members` list

Cleanup output example (`…` stands for machine-specific paths):
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

No manual YAML editing required.

---

## 9. VRAM calibration

The autoscan adds new models with a **first fit from `llama-fit-params`**
(see [section 7](#7-start-and-verify)): the largest context up to the native
one from the GGUF metadata that the projection places in VRAM with 1024 MiB
headroom per GPU. That value is a projection, not a measurement — on MoE
models in particular `llama-fit-params` is often off. Calibration loads the
model for real and finds the actual maximum.

To calibrate in the AIfred UI:

1. Select the new model in AIfred
2. Click **"Calibrate"** next to the model selector
3. Pick the variants you want via the **2D matrix picker**:
   - Rows = VLM choices (No VLM / Vigilantia 4B / Vigilantia 8B)
   - Columns = No TTS plus every local GPU TTS engine the TTS-Suite offers as installed (Qwen3-TTS, XTTS)
   - Each ticked cell becomes a separate `<base>-vlm-<key>-tts-<engine>`
     llama-swap profile that the chat-path resolver picks up automatically
4. Click **"Kalibrierung starten"**. The matrix shows three states per cell:
   - 🟢 green dot — calibrated
   - 🔴 red dot — tried but failed (hover for reason)
   - empty — never tried

What runs under the hood:

- **Greedy cascade**: fill the fastest compute class first, spill to the
  next, minimize active GPUs
- **Stress burn-in** on first TTS/VLM use: a worst-case bilingual TTS
  synthesis loop and a VLM context-fill prewarm measure peak VRAM under
  load. The VLM result is cached in
  `data/vlm_vram_cache.json`; the TTS suite names the TTS reserve (`needed_mib`) — next calibrations reuse the measurement
- **Side-channel capacity guard**: before writing a `tts-engine + vlm`
  combo profile, the calibrator checks whether both reserves fit on the
  shared side-channel GPU. Combos that would OOM at runtime are
  rejected with a red dot
- **Bias-tracked binary search**: when `llama-fit-params` is consistently
  off (typical on MoE models), the bias is tracked across probes and
  fed back into the math projection, so the search converges in 3-5
  probes instead of 25+
- Final results land in `data/model_vram_cache.json` and as profile
  entries in `~/.config/llama-swap/config.yaml`

> **Strategy reference (SSOT):** [calibration-strategy.md](../architecture/calibration-strategy.md)

Without calibration the model still works — it runs with the context from the
autoscan's `llama-fit-params` fit. If `llama-fit-params` is missing next to
`llama-server`, the autoscan uses safe defaults instead (context at most
32,768, q4_0 KV). Should the projection overestimate what fits, the model
fails with an OOM error on load or on the first long request — then run
calibration (or lower `-c` by hand, see [Troubleshooting](#oom-crash--context-too-large)).

---

## 9a. Speech output (TTS-Suite)

Speech output comes from the **TTS-Suite**, a separate service
([Peuqui/TTS-Suite](https://github.com/Peuqui/TTS-Suite)). Engines, their Docker containers, the
choice of the card and other machines with engines (hosts) belong to it; AIfred only sends text and
its list (semantics in [Configuration → Speech output](configuration.md#speech-output-tts) and
[TTS-Suite](../architecture/tts-suite.md)).

- **Installation:** `install-all.sh` (step 2d) clones the repository next to AIfred, runs its
  `deploy/install.sh` (virtual environment, command, token, first configuration with the voices of the
  clone and the pronunciation "AIfred → Alfred") and asks whether to install the system unit
  `tts-suite@<user>` with the polkit rule (one sudo step, `deploy/switch-to-system-unit.sh`; only with
  it can AIfred start the service itself when needed). Then it checks `/health` on port 5090. Without
  Docker with Compose the local engines do not start; cloud engines and the browser voice still work.
  By hand: `deploy/install.sh` and `sudo deploy/switch-to-system-unit.sh`.
- AIfred starts the unit when it is not running (`ensure_ready`, needs the polkit rule). Without the
  service AIfred runs, but speech output fails loudly (the browser voice speaks if its entry is on).
- **Hosts** (a second machine with a GPU that takes over TTS): they are configured in the
  service's own web interface / `~/.config/tts-suite/config.json` (name, address, SSH), not in AIfred.
- **Voices:** the reference voices live in the service's voice folder (`voices_dir` of its
  configuration); a voice added later is seen after the container has been restarted.


## 9b. Speech input (STT-Suite)

Speech recognition comes from the **STT-Suite** ([Peuqui/STT-Suite](https://github.com/Peuqui/STT-Suite)),
a container with Whisper and Parakeet on port 5080. `install-all.sh` (step 2e) clones the repository
next to AIfred (or `STT_SUITE_DIR` from `.env`) and starts it with `docker compose`. **Without an
NVIDIA GPU** (`nvidia-smi` shows none) the installer adds `docker-compose.cpu.yml`: the base file
reserves a GPU, which Docker refuses there. AIfred does the same when it starts the container itself
(`STTClient.start_container`).

- **What AIfred sets:** nothing about the device. AIfred always asks with `device=auto`: the
  STT-Suite takes a card only when there is room on it, else the CPU. Long recordings (over
  `WHISPER_GPU_MIN_FILE_MB`, 10 MB) AIfred sends to the CPU. Whoever wants the cards never touched
  sets that in the STT-Suite's automatic list. The STT tab of the Agent Editor only shows whether
  the service answers.
- **What the STT-Suite sets** (its page at `/ui/`, `/stt-suite/ui/` behind the proxy): engine, model and
  quality per device, the order of the automatic mode, the memory margin and the measurement of the
  memory need.
- **Freeing the GPU:** before a model loads on its card, GPU-Orc ends the GPU worker (the CPU models
  stay). A transcription that is still running with `device=auto` is then done again on the CPU from
  the same file, nothing is lost ([9c](#9c-clearing-the-cards-gpu-orc)).

## 9c. Clearing the cards (GPU-Orc)

Before a model loads, something else may hold its cards: the STT-Suite's GPU worker, a local TTS
engine, the persistent helper models of llama-swap (describer, embedder).
[GPU-Orc](https://github.com/Peuqui/GPU-Orc) is the one place per machine that knows how to clear
them. AIfred asks it before every cold start of a model through llama-swap, before it switches to a
TTS-calibrated profile and before a calibration; the describer's room check reads from it who holds
which card. GPU-Orc asks the services to let go through their own interfaces (`POST /gpu/release` of
the suites, llama-swap's unload) and checks the result (status and memory per card); it never ends a
process itself.

- **Installation:** `install-all.sh` (step 2g) clones the repository next to AIfred (or `GPU_ORC_DIR`
  from `.env`), runs its `deploy/install.sh` (own virtual environment, command `gpu-orc`, first
  configuration) and asks whether to install the system unit `gpu-orc@<user>` with the polkit rule
  (one sudo step, `deploy/switch-to-system-unit.sh`: it then starts at boot without a login, and you
  restart it without a password). Otherwise GPU-Orc runs as a user service. Then the installer checks
  `/health` on port 5070. A re-run pulls the repository and restarts the service.
- **Configuration:** `~/.config/gpu-orc/config.yaml`, written from the example at the first install.
  It names the STT-Suite (`127.0.0.1:5080`), the TTS-Suite (`127.0.0.1:5090`) and llama-swap
  (`127.0.0.1:11435`, with its `~/.config/llama-swap/config.yaml`). Per suite: how long to wait while
  it is busy (`busy_wait_s`) and whether to force afterwards (`force_after_wait`):
  - STT 30 s: a dictation is done on the GPU in seconds; a long recording with `device=auto` is then
    transcribed again on the CPU from the same file.
  - TTS 5 s: a reading then ends with `reading_aborted`, an engine that is still starting is shot
    down (`docker kill`).
  - A request ends after `free_timeout_s` (180 s) at the latest. After a change:
    `systemctl restart gpu-orc@<user>` (or `systemctl --user restart gpu-orc`).
- **Traffic light:** While a big model loads, its cards look emptier than they are about to be. So
  whatever wants to put something on a card asks `GET /loading` first: AIfred's describers, the TTS
  suite (`gpu_orc_url` in its configuration) and the STT suite (`STT_GPU_ORC_URL`, in the container
  with `STT_GPU_ORC_DIR` and `STT_GPU_ORC_TOKEN_FILE`). While something loads or GPU-Orc does not
  answer, TTS goes to the next list entry, STT to the CPU, and a describer only comes afterwards.
- **AIfred's side:** `GPU_ORC_URL` in `.env` (default `http://127.0.0.1:5070`) and
  `GPU_ORC_MODEL_SERVER` (the consumer name of the model server in GPU-Orc's configuration, default
  `llama-swap`). AIfred takes its HTTP timeout from GPU-Orc's `/health`.
- **AIfred as a consumer:** its face detection runs in a process of its own and gives its card back
  through the suites' interface (`/api/gpu/holders`, `/api/gpu/release`, item `face-worker`, released
  once nvidia-smi no longer lists the process). Put `GPU_RELEASE_API_TOKEN` into `.env`, the same token
  into a file only the service user can read, and add to GPU-Orc's configuration (0.8.0 or later):
  `{name: aifred, type: gpu-release, url: http://127.0.0.1:8002/api, token_file: <that file>, busy_wait_s: 5}`.
  While the light is red the face detection starts on the CPU and moves back onto its card at the first
  detection after green.
- **Without GPU-Orc** AIfred runs and loads anyway, but every load says loudly in the log that the
  cards could not be cleared, and the describer's room check finds no room. **Without an NVIDIA GPU**
  GPU-Orc runs, but there is nothing to clear.
- **Check:** `curl -s http://127.0.0.1:5070/status` shows per card who holds what. A preview without
  effect: `curl -s -X POST http://127.0.0.1:5070/free -H 'Content-Type: application/json' -d
  '{"model":"<model>","reason":"test","dry_run":true}'`.
- **Callers on other machines** (for example an agent on another host that loads models here):
  GPU-Orc then listens on all addresses and asks for a token (`listen.host` and `token_file` in its
  configuration; loopback stays without a token). Details:
  [GPU-Orc interface](https://github.com/Peuqui/GPU-Orc/blob/main/docs/interface.md).

---

## 10. Vision setup (optional)

The vision pipeline is **off by default**. Turn it on when you want
image analysis in chat, on-demand VLM queries via tools, or the
Vigilantia camera-surveillance plugin.

### Hardware

- A V4L2-capable camera at `/dev/video0` (or any `/dev/video*`) for
  webcam input. USB UVC cameras and integrated laptop webcams just work
- For face recognition: an NVIDIA GPU (CUDA Execution Provider) is
  recommended. CPU-only works but is much slower
- **`video` group membership**: the AIfred service account must be in
  the `video` group. The `install-all.sh` script verifies this and
  shows a fix hint if it's missing

```bash
groups | grep -qw video || sudo usermod -aG video $USER
# log out + back in (or run 'newgrp video') for the change to take effect
```

### Pull a VLM (Vision-Language Model) via Ollama

VLM inference runs on Ollama (the llama.cpp `--mmproj` path is currently
unreliable for Qwen3-VL — see the architecture notes). Pull one of the
calibrated VLMs:

```bash
ollama pull qwen3-vl:4b-instruct-q8_0    # ~6.5 GB VRAM, fast
ollama pull qwen3-vl:8b-instruct-q8_0    # ~11 GB VRAM, more accurate
```

### Enable in the UI

1. Settings → Vision → set `vision_mode` to:
   - `off` — disabled (default)
   - `on-demand` — VLM loaded only when a vision tool is called
   - `live` — VLM stays resident in VRAM (lower latency, higher idle
     cost)
2. Pick the active VLM model under Settings → Vision → Model
3. (Optional) Configure face recognition:
   - Settings → Vision → Face Recognition → Execution Provider
     (CUDA / CPU / CoreML)
   - Threshold for "known" vs "unsure" classification

### Calibrate the LLM with VLM-awareness

When `vision_mode` is `on-demand` or `live`, the LLM profile needs to
reserve VRAM on the side-channel GPU for the VLM container. Re-run the
calibration (Section 9) with the **Vigilantia 4B** or **Vigilantia 8B**
row ticked — that produces a `<base>-vlm-<key>` profile and the
resolver picks it automatically when vision is active.

---

## 11. Vigilantia (camera surveillance) setup (optional)

Layered on top of the vision pipeline. Turns AIfred into a continuous
monitoring agent with motion detection, face recognition and event
review.

### First-time setup

1. Enable the **Vigilantia** channel plugin in the Plugin Manager
2. Restart the Message Hub workers (it's loaded at start)
3. Open the **Casus** modal — it lists detected camera sources

### Enroll faces (Personarium)

The face-recognition pipeline only labels a face as "known" if you've
**enrolled** it first. Without enrollment, every face shows up as
`unknown` events.

1. Snapshot a frame from a camera with a clear face
2. Open the **Personarium** modal
3. Multi-pose wizard: capture frontal + 4 angles
4. Assign a name + (optional) group
5. The face vectors land in the SQLite store; the next watcher pass
   classifies matching faces as `known`

**First-run cost:** on the first call, `insightface` downloads the
`buffalo_l` model (~280 MB) into `~/.insightface/models/`. Subsequent
runs are fast.

### Start a watcher

```
LLM: vision_start_watch(source="webcam0", motion=true, face=true, vlm_on_motion=false)
```

Or via the Casus UI: select a source → "Start watcher". The watcher
runs in the Message Hub worker process, so it **survives browser
disconnects**.

### Configure thresholds

Settings → Vision → Vigilantia:

- `motion.min_area_ratio` — fraction of the frame that must change
  before a motion event fires (default 0.02 = 2%)
- `motion.warmup_frames` — frames to learn the background before
  triggering (default 10)
- `min_event_interval_sec` — debounce between events (default 1s).
  Configurable **per camera** in the camera editor ("Min. event interval");
  the live-preview popup has its own, independent throttle in its header
- `save_event_frames` — store the frame as JPEG on each event
- `face_detect.threshold_known` — cosine similarity above which a face
  is `known` (default 0.6)
- `face_detect.threshold_unsure` — below `known` but above this →
  `unsure` (default 0.5). Below → `unknown`
- `events.retention_days_*` — per-event-type retention

### Use the Casus event browser

The **Casus** modal is the central event review tool:

- Filter by type (motion / face_known / face_unsure / face_unknown / vlm_analysis)
- Filter by source, by face id, by time
- **Single-event VLM analysis**: click any event → "Analyze with VLM"
  — runs the configured VLM on the saved frame
- **Bulk VLM analysis**: select N events → background worker runs the
  VLM on each with progress + cancel. A VRAM pre-check aborts cleanly
  if there's not enough headroom for the configured VLM batch
- **Cluster mode toggle**: collapses near-duplicate events (pHash-based)
  into one card per cluster — useful when a tree branch moving in the
  wind would otherwise produce 200 motion events in 10 minutes

---

## 12. Troubleshooting

### Model does not appear in AIfred

```bash
# Check the YAML
cat ~/.config/llama-swap/config.yaml

# Check autoscan output
sudo journalctl -u llama-swap -b | grep -A5 "Autoscan"

# Run autoscan manually
source ~/Projekte/AIfred-Intelligence/venv/bin/activate
python ~/Projekte/AIfred-Intelligence/scripts/llama-swap-autoscan.py
```

### Model ended up in the skip list

```bash
cat ~/.config/llama-swap/autoscan-skip.json
# Remove the entry to re-test after a llama.cpp update:
nano ~/.config/llama-swap/autoscan-skip.json
sudo systemctl restart llama-swap
```

### llama-server binary not found

The autoscan reads the binary path from existing YAML entries. If no entries exist
yet, it falls back to `~/llama.cpp/build/bin/llama-server`. If the binary is
elsewhere, add a temporary entry with the correct path:

```yaml
# ~/.config/llama-swap/config.yaml
models:
  _dummy:
    cmd: /your/path/to/llama-server --port ${PORT} --model /dev/null
    ttl: 1
```

Run autoscan once, then remove the `_dummy` entry.

### Every load logs "GPU-Orc could not clear the cards"

GPU-Orc does not answer. Check it and start it:

```bash
curl -s http://127.0.0.1:5070/health
systemctl status gpu-orc@$USER          # system unit (or: systemctl --user status gpu-orc)
journalctl -u gpu-orc@$USER -n 50       # (or: journalctl --user -u gpu-orc -n 50)
```

### OOM crash / context too large

```bash
# Run calibration in AIfred UI, or reduce the context manually:
nano ~/.config/llama-swap/config.yaml
# Adjust the -c parameter for the affected model
sudo systemctl restart llama-swap
```

---

## Related documents

- [llamacpp-setup.md](llamacpp-setup.md) — Hardware benchmarks, performance flags,
  multi-GPU configuration, Flash Attention details
