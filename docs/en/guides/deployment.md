# AIfred Deployment Guide

> **Deutsche Version:** [deployment.md](../../de/guides/deployment.md)

Setup guide for a fresh AIfred installation with the llama.cpp backend (llama-swap).

**Last updated:** 2026-09-25

> **TL;DR — fastest path:** `./scripts/install-all.sh` from a fresh
> clone handles dependencies, venv, Playwright, the Reflex routing
> patch, optional systemd services, `.env`, the `bge-m3` embedding pull
> and a first whitelist user in one go. The sections below describe
> the same flow **manually** for debugging and for non-standard setups
> (multi-GPU rigs, camera surveillance, vLLM coexistence).

---

## Overview

AIfred uses **llama-swap** as a proxy daemon for llama.cpp. llama-swap manages
multiple models and loads them on demand. The **autoscan** mechanism detects new
models automatically and configures them without any manual YAML editing.

```
User <-> AIfred (Reflex web app) <-> llama-swap (:11435) <-> llama-server (per model)
```

---

## 1. Prerequisites

### Hardware
- NVIDIA GPU with CUDA support (Compute Capability >= 6.1, Pascal or newer)
- Recommended: >= 24 GB VRAM for useful model sizes

### Software
- Linux with systemd (Ubuntu/Debian recommended)
- CUDA Toolkit >= 12.0
- Python 3.10+
- Git

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
source venv/bin/activate
pip install -r requirements.txt
```

---

## 5. Set up systemd services

For the AIfred-side services (chromadb + intelligence + optional
corpus-server) use the installer script — it's update-safe:

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
service is only restarted when its own unit or drop-in changed — re-runs on
a clean system are no-ops.

| Unit | Runs |
|---|---|
| `aifred-chromadb.service` | `docker compose up -d chromadb searxng` — vector store + web search |
| `aifred-intelligence.service` | the Reflex app (frontend `3002`, backend `8002`) |
| `aifred-corpus-server.service` | optional corpus search API (`127.0.0.1:8005`, for `deploy/corpus/`) |

The installer renders `systemd/aifred-intelligence.service` (and the
chromadb / corpus units) into `/etc/systemd/system/`, substitutes the
real user + project paths, reloads systemd and enables the units. These
are **system-level services** (`WantedBy=multi-user.target`, running as
`User=<you>`) — manage them with `sudo systemctl`, not `systemctl --user`.

> **Tip:** `enable`/`disable` and editing units always need `sudo` (they
> write to `/etc/systemd/system/`). The runtime operations `restart`,
> `stop` and `status` can be run **without** `sudo` if you add a PolKit rule
> allowing your user to manage these specific units — handy for the
> frequent `restart llama-swap` / `restart aifred-intelligence` during
> tuning. Without such a rule, prefix them with `sudo`.

The AIfred unit runs Reflex directly via the venv Python:

```
ExecStartPre=/bin/bash <project>/scripts/patch-vite-config.sh
ExecStart=<project>/venv/bin/python -m reflex run \
    --frontend-port 3002 --backend-port 8002 --backend-host 0.0.0.0
```

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
| `AIFRED_ALLOWED_HOST` | Your external domain — added to Vite's `allowedHosts` on every start |
| `INJECT_API_TOKEN` | Token for `/api/chat/inject` (see [REST API](rest-api.md)) |
| `WEBHOOK_API_TOKEN` | Token for `/api/agent/trigger` |
| `AIFRED_SESSION_SECRET` | Signs the login cookies (optional — a random secret is persisted otherwise) |
| `LLAMACPP_URL` | llama-swap URL (default `http://localhost:11435/v1`) |
| `LLAMACPP_CALIBRATION_PORT` | Port of the temporary calibration server (default `9999`) |
| `AIFRED_FRONTEND_PATH` | URL prefix when the app lives under a sub-path of a reverse proxy, e.g. `aifred` for `/aifred/` (default: none) |
| `BACKEND_URL` | Only without a reverse proxy: backend URL for `/_upload/` as seen by the browser |
| `BRAVE_API_KEY`, `TAVILY_API_KEY` | Optional extra search APIs (SearXNG needs no key) |
| `ANTHROPIC_API_KEY`, `DASHSCOPE_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY` | Cloud LLM providers |
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

### How the frontend finds the backend

`rxconfig.py` sets `api_url` to `http://0.0.0.0:8002`; the Reflex frontend
replaces `0.0.0.0` with the host the page was loaded from — no URL setting
needed:
- Over HTTPS (reverse proxy) it switches to `https`/`wss` on the standard port
  **443** — the proxy must listen on 443 too, even if you open the page on
  another port such as 8443.
- Over plain HTTP (e.g. `http://<LAN-IP>:3002`) the browser talks to port
  **8002** on that host directly — that is why the backend listens on all
  interfaces (`--backend-host 0.0.0.0`). Every `/api` route except the token
  endpoints requires the login cookie.

### Why dev mode

The service runs `reflex run` without `--env prod`. Production mode causes a
flash of unstyled content on every reload (React Router 7 with
`prerender: true` loads the CSS asynchronously). Dev mode costs a little more
RAM, non-minified bundles and more console warnings — negligible for a home
server.

Two consequences:
- `scripts/patch-vite-config.sh` runs before every start and patches the
  generated `.web/vite.config.js`: `allowedHosts` from `AIFRED_ALLOWED_HOST`,
  and `dedupe` for shared frontend libraries (without it `react-helmet` ends
  up in several lazy chunks and the browser crashes with
  *"Identifier 'scrollState' has already been declared"*). Idempotent.
- `/api`, `/_upload` and `/_event` are **not** proxied by Vite — the reverse
  proxy routes them to the backend (see [Access the web UI](#access-the-web-ui)).

### Reflex patches

Two patches against Reflex bugs are needed; re-check them after every Reflex
upgrade:

| Patch | Applied by | Why |
|---|---|---|
| `route.py` — `frontend_path` route matching | `scripts/patch-reflex.py` (installer) | Without it `on_load` never fires and the app hangs at "initialising…" |
| `utils/exec.py` — `run_granian_backend()`: `reload=False`, `respawn_failed_workers=True`, `respawn_interval=3.5` | manual | Without it a backend worker that dies from a C-level crash is never respawned and AIfred stays dead until a manual restart |

The second patch also turns off backend hot reload — restart the service after
code changes.

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

AIfred runs as **two processes** behind a single port that a reverse proxy
ties together:

| Process | Default port | Serves |
|---------|-------------|--------|
| Reflex **frontend** (Node) | `3002` | the app pages + WebSocket state channel |
| Reflex **backend** (Granian/FastAPI) | `8002` | `/api/*` (REST, Casus frames, audio SSE), `/_upload/*` (images, face crops, documents), `/_event` |

**The frontend port alone is not enough.** If you open the app directly on
`http://<host>:3002/aifred/`, the pages load and the WebSocket works, but every
`/api/*` and `/_upload/*` request 404s — so camera thumbnails, the Vigilantia
live modal, Casus previews and audio playback stay blank. Those routes only
exist on the backend, and only a reverse proxy in front of both processes makes
them reachable under one origin.

**Recommended: a reverse proxy (nginx/Caddy)** that routes by path prefix to the
two upstreams. Minimal nginx sketch (generic — substitute your own host,
ports and, if desired, TLS/auth):

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

With the proxy in place, open **`http://your-host.example/aifred/`** (no port).
The `/_upload/*` static mounts are additionally cookie-gated by the web login
(`AuthenticatedStaticFiles`), so only the share-links under
`/_upload/html_preview` are reachable without a session.

> **Quick local check without a proxy:** the app is usable on `:3002` for
> everything that goes over the WebSocket (chat, settings), but treat missing
> images/audio there as expected, not a bug — reach it through the proxy to see
> the full UI.

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
   - Columns = TTS engines (No TTS / Qwen3-TTS / XTTS / MOSS-TTS / Fish-Speech)
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
  load. Results cached in `data/tts_vram_cache.json` /
  `data/vlm_vram_cache.json` — next calibrations reuse the measurement
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
