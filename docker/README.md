# Docker Services for AIfred Intelligence

This directory contains all Docker services required by AIfred Intelligence.

## Services

### 1. ChromaDB (Essential)
Vector database for indexed documents (RAG) and agent memory.

**Start:**
```bash
docker compose up -d chromadb
```

**Stop:**
```bash
docker compose stop chromadb
```

**Check status:**
```bash
docker compose ps
```

### 2. SearXNG (Optional)
Local meta-search engine for web research.

**Start (with ChromaDB):**
```bash
docker compose --profile full up -d
```

**Access:** http://localhost:8888

## Management

### Start all services
```bash
# ChromaDB only (default)
docker compose up -d chromadb

# ChromaDB + SearXNG
docker compose --profile full up -d
```

### Stop all services
```bash
docker compose down
```

### View logs
```bash
# All services
docker compose logs -f

# ChromaDB only
docker compose logs -f chromadb

# SearXNG only
docker compose logs -f searxng
```

### Reset ChromaDB (deletes indexed documents and agent memory)
```bash
# Option 1: Stop container + delete data
docker compose stop chromadb
sudo rm -rf ../data/chromadb/   # files are created by the container (root)
docker compose up -d chromadb

# Option 2: Delete a single collection only (see main README)
```

## Network

All services run in the shared `aifred-network`, allowing inter-service communication.

- ChromaDB: `http://localhost:8000`
- SearXNG: `http://localhost:8888` (only with `--profile full`)

## Volumes

- `../data/chromadb/` - Persistent storage for ChromaDB
- `./searxng/settings.yml` - SearXNG configuration
