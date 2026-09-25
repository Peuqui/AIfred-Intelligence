# Systemd Service Files for AIfred Intelligence

This directory contains the systemd service files for running AIfred Intelligence in production.

## Included Services

### 1. `aifred-chromadb.service`
Starts the ChromaDB (vector store) and SearXNG (web search) Docker containers
via `docker compose up -d chromadb searxng`.

- Starts on boot, waits for Docker, restarts on failure

### 2. `aifred-intelligence.service`
The main AIfred service (Reflex app, frontend `3002`, backend `8002`).

- Runs `scripts/patch-vite-config.sh` before every start
- Waits for Ollama and ChromaDB (soft dependency, see Notes)
- Drop-in `aifred-intelligence.service.d/hardening.conf`: larger Bun/Node heap,
  `Wants=` instead of `Requires=`
- Automatic restart on failure, logging via journald

### 3. `aifred-corpus-server.service` (optional)
FastAPI corpus search API on `127.0.0.1:8005` — backend for the corpus UI in
`deploy/corpus/`. The installer asks before installing it.

## Installation

The unit files are **templates**: they contain `__USER__`, `__PROJECT_DIR__`
and `__DOCKER_BIN__` placeholders. Never copy them to `/etc/systemd/system/`
by hand — a plain `cp` leaves the placeholders in place and the services never
start. Use the installer:

```bash
sudo ./scripts/install-services.sh              # install or update (backs up changed files)
./scripts/install-services.sh --dry-run         # show what would change, no sudo, no writes
sudo ./scripts/install-services.sh --no-overwrite   # keep locally modified units

systemctl status aifred-chromadb.service aifred-intelligence.service
```

It renders and installs units and drop-ins, runs `daemon-reload` when a unit
changed, enables the services, starts them, restarts only services whose own
unit changed, and links `scripts/llama-swap-restart` to `~/bin`.

### After Code Updates

```bash
# Restart AIfred only (ChromaDB keeps running)
sudo systemctl restart aifred-intelligence.service
```

### After Service File Changes

Edit the template here in `systemd/`, then re-run
`sudo ./scripts/install-services.sh`.

## Monitoring

### View Logs

```bash
# AIfred logs (live)
journalctl -u aifred-intelligence.service -f

# ChromaDB logs
journalctl -u aifred-chromadb.service -f

# Both together
journalctl -u aifred-intelligence.service -u aifred-chromadb.service -f

# Last 100 lines
journalctl -u aifred-intelligence.service -n 100
```

### Service Status

```bash
# All AIfred services at a glance
systemctl status aifred-*

# Detailed status
systemctl status aifred-intelligence.service
systemctl status aifred-chromadb.service
```

## Troubleshooting

### AIfred Won't Start

```bash
# 1. Check ChromaDB status
systemctl status aifred-chromadb.service
docker ps | grep chromadb

# 2. Check Ollama status
systemctl status ollama.service

# 3. Check logs
journalctl -u aifred-intelligence.service -n 50
```

### ChromaDB Container Not Running

```bash
# Start containers manually
cd <project>/docker
docker compose up -d chromadb searxng

# Or via service
sudo systemctl restart aifred-chromadb.service
```

### AIfred Not Working After System Boot

```bash
# Check startup dependencies
systemctl list-dependencies aifred-intelligence.service

# Should show:
# aifred-intelligence.service
# ├─aifred-chromadb.service
# │ └─docker.service
# ├─ollama.service
# └─network.target
```

## Configuration

### Environment Variables

The service reads the optional `.env` in the project root (`EnvironmentFile=-…/.env`) —
secrets and machine-specific overrides, see `.env.example`. Behind nginx under a
sub-path, set `AIFRED_FRONTEND_PATH` (e.g. `aifred`). No URL or mode variable is
needed: the frontend derives the backend address from the page's own hostname
(see [Deployment → How the frontend finds the backend](../docs/en/guides/deployment.md#how-the-frontend-finds-the-backend)).
All variables: [Deployment → Environment](../docs/en/guides/deployment.md#environment-env).

## Command Reference

```bash
# Start
sudo systemctl start aifred-intelligence.service

# Stop
sudo systemctl stop aifred-intelligence.service

# Restart
sudo systemctl restart aifred-intelligence.service

# Status
systemctl status aifred-intelligence.service

# Enable on boot
sudo systemctl enable aifred-intelligence.service

# Disable on boot
sudo systemctl disable aifred-intelligence.service
```

## Ollama Optimization

### `ollama-override.conf.example`

**Critical performance optimization for single-user setups!**

Ollama's default `OLLAMA_NUM_PARALLEL=2` doubles KV-cache allocation for an unused parallel slot, wasting ~50% of GPU VRAM.

**Impact:** Setting `OLLAMA_NUM_PARALLEL=1` can double your available context window (e.g., 111K → 222K for 30B models).

```bash
# Install the override
sudo mkdir -p /etc/systemd/system/ollama.service.d/
sudo cp systemd/ollama-override.conf.example /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama

# Verify
systemctl status ollama  # Should show "Drop-In: override.conf"
```

After applying, **recalibrate your models** in the AIfred UI to take advantage of the freed VRAM.

---

## Notes

- AIfred prefers ChromaDB to start first — expressed via `Wants=` (a soft
  dependency, not `Requires=`). This is deliberate: a ChromaDB recreate
  (e.g. the nightly docker auto-update) would otherwise cascade-stop AIfred
  and kill running indexing jobs. AIfred starting without ChromaDB is less
  ideal than that cascade-restart, hence `Wants=`.
- AIfred restarts automatically on failure (`Restart=always`)
- Logs are persistently stored in journald
- Services start automatically on system boot (when enabled)
