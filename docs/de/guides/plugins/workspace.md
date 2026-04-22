# Workspace Plugin

**Datei:** `aifred/plugins/tools/workspace/`

Das Workspace Plugin bietet dem LLM direkten Dateizugriff auf das Dokumenten-Verzeichnis (`data/documents/`) sowie die zentrale Verwaltung aller ChromaDB-Vektordatenbank-Collections.

## Tools

### Dateisystem

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `list_files` | Dateien und Ordner im Dokumenten-Verzeichnis auflisten | READONLY |
| `read_file` | Datei lesen (PDFs seitenweise, Text mit Zeilenbereichen) | READONLY |
| `write_file` | Textdatei schreiben oder bearbeiten (mit Verify) | WRITE_DATA |
| `create_folder` | Unterordner anlegen | WRITE_DATA |
| `delete_file` | Datei löschen | WRITE_SYSTEM |
| `delete_folder` | Leeren Ordner löschen | WRITE_SYSTEM |

### ChromaDB (Vektordatenbank)

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `index_document` | Datei in ChromaDB einspeisen (Chunking + Embedding) | WRITE_DATA |
| `search_documents` | Indexierte Dokumente semantisch durchsuchen | READONLY |
| `list_indexed` | Alle indexierten Dokumente anzeigen | READONLY |
| `delete_document` | Dokument aus Vektordatenbank + Platte entfernen | WRITE_SYSTEM |
| `chromadb_stats` | Alle Collections mit Eintragsanzahl anzeigen | READONLY |
| `chromadb_clear` | Alle Einträge einer Collection löschen | WRITE_SYSTEM |

## Features

### Dateizugriff
- **PDF seitenweise lesen:** `read_file(filename="report.pdf", pages="1-5")` oder `pages="3,7,10-12"`
- **Große Textdateien abschnittweise:** `read_file(filename="log.txt", line_start=100, line_end=200)`
- **Path-Traversal-Schutz:** Alle Pfade werden gegen `data/documents/` validiert — kein Ausbruch möglich
- **Write-Verify:** Jede geschriebene Datei wird zurückgelesen und die Länge verglichen
- **Erlaubte Schreibformate:** .txt, .md, .csv, .json, .xml, .html

### ChromaDB-Verwaltung
- **Index:** Unterstützt PDF, TXT, MD, CSV, DOCX, XLSX, PPTX, ODT, ODS, ODP
- **Chunking:** Automatisch in ~500-Token-Abschnitte mit Overlap
- **Semantische Suche:** Embedding-basiert über alle indexierten Dokumente
- **Zentrale Verwaltung:** `chromadb_stats` zeigt Research Cache, Documents und alle Agent-Memory-Collections auf einen Blick

## Sicherheit

- Alle Dateioperationen sind auf `data/documents/` beschränkt
- Path-Traversal-Versuche (z.B. `../../etc/passwd`) werden blockiert
- Löschen erfordert WRITE_SYSTEM Tier (höchste Stufe vor ADMIN)
- ChromaDB-Clear erfordert ebenfalls WRITE_SYSTEM
