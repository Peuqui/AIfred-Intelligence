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
| `rename` | Datei oder Ordner umbenennen (hält den ChromaDB-Index synchron) | WRITE_DATA |
| `copy_file` | Datei kopieren (serverseitig, binärsicher — auch MP3/PDF) | WRITE_DATA |
| `move_file` | Datei oder Ordner verschieben (Index folgt verschobenen Dateien) | WRITE_DATA |
| `delete_file` | Datei löschen (entfernt sie auch aus dem Index) | WRITE_SYSTEM |
| `delete_folder` | Ordner löschen (standardmäßig leer, `recursive=true` löscht den ganzen Baum) | WRITE_SYSTEM |

### ChromaDB (Vektordatenbank)

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `index_document` | Datei in ChromaDB einspeisen (Chunking + Embedding) | WRITE_DATA |
| `search_documents` | Indexierte Dokumente semantisch durchsuchen (Ordner-Filter, Pagination, MMR) | READONLY |
| `list_indexed` | Alle indexierten Dokumente anzeigen | READONLY |
| `list_orphaned` | Indexierte Dokumente anzeigen, deren Quelldatei auf der Platte fehlt | READONLY |
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
- **Chunking:** Automatisch in ~800-Token-Abschnitte mit Overlap
- **Semantische Suche:** Embedding-basiert über alle indexierten Dokumente
- **Ordner-Filter:** `search_documents(folder="bibel")` schränkt die Suche ein und schließt alle verschachtelten Unterordner automatisch mit ein
- **Relevanz-Labels:** Jeder Treffer wird mit `high` / `medium` (Ähnlichkeit) oder `context` (Nachbar-Chunks rund um einen Treffer) markiert
- **MMR-Diversifizierung:** Ergebnisse werden über Dateien/Vektor-Regionen verteilt, statt viele fast identische Chunks zurückzugeben
- **Pagination:** Dieselbe Query mit `page=2`, `page=3`, … erneut aufrufen — die Antwort liefert `has_more` plus `next_page_hint` oder `pagination_note`, ob tiefere Seiten lohnen
- **Orphan-Bereinigung:** `list_orphaned` findet Index-Einträge, deren Quelldatei auf der Platte gelöscht wurde
- **Zentrale Verwaltung:** `chromadb_stats` zeigt Research Cache, Documents und alle Agent-Memory-Collections auf einen Blick

## Sicherheit

- Alle Dateioperationen sind auf `data/documents/` beschränkt
- Path-Traversal-Versuche (z.B. `../../etc/passwd`) werden blockiert
- Löschen erfordert WRITE_SYSTEM Tier (höchste Stufe vor ADMIN)
- ChromaDB-Clear erfordert ebenfalls WRITE_SYSTEM
