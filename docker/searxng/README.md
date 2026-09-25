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
- **Auto-restart**: Container starts automatically on reboot
- **Logs**: Max 10MB × 3 files (rotation)
- **Default Language**: German (de)
- **Enabled Engines**: Google, Bing, DuckDuckGo, Wikipedia, News
- **Disabled**: Reddit, Twitter, YouTube (for faster responses)

## Troubleshooting

**Port already in use?**
```bash
# Change port in compose.yml:
ports:
  - "8889:8080"  # instead of 8888
```

**Container not running?**
```bash
docker compose logs searxng
```

**Change secret key?**

The secret comes from `SEARXNG_SECRET` in `docker/.env` (gitignored; generated
once by `scripts/install-all.sh`) and overrides `secret_key` in `settings.yml`.
To rotate it, replace the value and recreate the container:
```bash
sed -i "s/^SEARXNG_SECRET=.*/SEARXNG_SECRET=$(openssl rand -hex 32)/" docker/.env
cd docker && docker compose up -d searxng
```
