# SearXNG Docker Setup

Self-hosted meta-search engine for AIfred Intelligence.

## Quick Start

```bash
# Start SearXNG
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f

# Stop SearXNG
docker compose down
```

## Access

- **Web Interface**: http://localhost:8888
- **JSON API**: http://localhost:8888/search?q=test&format=json&language=de

## Configuration

- `compose.yml` - Docker Compose configuration
- `settings.yml` - SearXNG settings (search engines, timeout, etc.)

After changing `settings.yml`:
```bash
docker compose restart
```

## Details

- **Port**: 8888 (Host) → 8080 (Container)
- **Auto-restart**: Container startet automatisch beim Reboot
- **Logs**: Max 10MB × 3 Dateien (Rotation)
- **Default Language**: Deutsch (de)
- **Enabled Engines**: Google, Bing, DuckDuckGo, Wikipedia, News
- **Disabled**: Reddit, Twitter, YouTube (für schnellere Antworten)

## Troubleshooting

**Port bereits belegt?**
```bash
# Port in compose.yml ändern:
ports:
  - "8889:8080"  # statt 8888
```

**Container läuft nicht?**
```bash
docker compose logs searxng
```

**Secret Key ändern?**
```bash
# Neuen Secret generieren:
openssl rand -hex 32

# In settings.yml eintragen:
server:
  secret_key: "dein_neuer_secret_hier"
```
