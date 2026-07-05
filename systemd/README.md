# Systemd Service Files for AIfred Intelligence

This directory contains the systemd service files for running AIfred Intelligence in production.

## Included Services

### 1. `aifred-chromadb.service`
Starts and manages the ChromaDB Docker container for the vector cache.

**Features:**
- Starts automatically on system boot
- Waits for Docker service
- Restarts on failure

### 2. `aifred-intelligence.service`
The main AIfred service (Reflex app).

**Features:**
- Waits for Ollama and ChromaDB
- Sets `AIFRED_ENV=prod` for production mode
- Automatic restart on failure
- Logging via journalctl

## Installation

### First-time Setup

```bash
# 1. Copy service files
sudo cp systemd/aifred-chromadb.service /etc/systemd/system/
sudo cp systemd/aifred-intelligence.service /etc/systemd/system/

# 2. Enable services
sudo systemctl daemon-reload
sudo systemctl enable aifred-chromadb.service
sudo systemctl enable aifred-intelligence.service

# 3. Start services
sudo systemctl start aifred-chromadb.service
sudo systemctl start aifred-intelligence.service

# 4. Check status
systemctl status aifred-chromadb.service
systemctl status aifred-intelligence.service
```

### After Code Updates

```bash
# Restart AIfred only (ChromaDB keeps running)
sudo systemctl restart aifred-intelligence.service
```

### After Service File Changes

```bash
# Copy updated service files
sudo cp systemd/*.service /etc/systemd/system/

# Reload daemon and restart services
sudo systemctl daemon-reload
sudo systemctl restart aifred-chromadb.service
sudo systemctl restart aifred-intelligence.service
```

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
# Start container manually
cd /home/mp/Projekte/AIfred-Intelligence/docker
docker compose up -d chromadb

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

The key environment variable is **`AIFRED_ENV=prod`** in `aifred-intelligence.service`:

- `AIFRED_ENV=dev`: API URL = `http://172.30.8.72:8002` (development machine/WSL)
- `AIFRED_ENV=prod`: API URL = `https://narnia.spdns.de:8443` (production server)

**Important**: Without `AIFRED_ENV=prod`, all API requests are routed to the development machine!

### Modifying Services

When modifying service files:
1. Edit files in this `systemd/` directory
2. Copy them to `/etc/systemd/system/`
3. Run `sudo systemctl daemon-reload`
4. Restart the services

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
