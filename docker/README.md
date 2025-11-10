# Docker Services für AIfred Intelligence

Diese Datei enthält alle Docker-Services die AIfred Intelligence benötigt.

## Services

### 1. ChromaDB (Essential)
Vector Database für semantischen Cache von Web-Recherchen.

**Starten:**
```bash
docker-compose up -d chromadb
```

**Stoppen:**
```bash
docker-compose stop chromadb
```

**Status prüfen:**
```bash
docker-compose ps
```

### 2. SearXNG (Optional)
Lokale Meta-Suchmaschine für Web-Recherchen.

**Starten (mit ChromaDB):**
```bash
docker-compose --profile full up -d
```

**Zugriff:** http://localhost:8888

## Verwaltung

### Alle Services starten
```bash
# Nur ChromaDB (Standard)
docker-compose up -d chromadb

# ChromaDB + SearXNG
docker-compose --profile full up -d
```

### Alle Services stoppen
```bash
docker-compose down
```

### Logs anzeigen
```bash
# Alle Services
docker-compose logs -f

# Nur ChromaDB
docker-compose logs -f chromadb

# Nur SearXNG
docker-compose logs -f searxng
```

### ChromaDB Cache zurücksetzen
```bash
# Option 1: Container neu starten + Daten löschen
docker-compose stop chromadb
cd ..
rm -rf aifred_vector_cache/
cd docker
docker-compose up -d chromadb

# Option 2: Nur Collection löschen (siehe Haupt-README)
```

## Netzwerk

Alle Services laufen im gemeinsamen `aifred-network`, sodass sie untereinander kommunizieren können.

- ChromaDB: `http://localhost:8000`
- SearXNG: `http://localhost:8888` (nur mit `--profile full`)

## Volumes

- `../aifred_vector_cache/` - Persistenter Storage für ChromaDB
- `./searxng/settings.yml` - SearXNG Konfiguration
