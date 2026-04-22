# Workspace Plugin

**File:** `aifred/plugins/tools/workspace/`

The Workspace Plugin provides the LLM with direct file access to the documents directory (`data/documents/`) and central management of all ChromaDB vector database collections.

## Tools

### File System

| Tool | Description | Tier |
|------|------------|------|
| `list_files` | List files and folders in the documents directory | READONLY |
| `read_file` | Read a file (PDFs page-by-page, text with line ranges) | READONLY |
| `write_file` | Write or edit a text file (with verify) | WRITE_DATA |
| `create_folder` | Create a subfolder | WRITE_DATA |
| `delete_file` | Delete a file | WRITE_SYSTEM |
| `delete_folder` | Delete an empty folder | WRITE_SYSTEM |

### ChromaDB (Vector Database)

| Tool | Description | Tier |
|------|------------|------|
| `index_document` | Index a file into ChromaDB (chunking + embedding) | WRITE_DATA |
| `search_documents` | Search indexed documents semantically | READONLY |
| `list_indexed` | List all indexed documents | READONLY |
| `delete_document` | Remove document from vector database + disk | WRITE_SYSTEM |
| `chromadb_stats` | Show all collections with entry counts | READONLY |
| `chromadb_clear` | Clear all entries from a collection | WRITE_SYSTEM |

## Features

### File Access
- **Page-by-page PDF reading:** `read_file(filename="report.pdf", pages="1-5")` or `pages="3,7,10-12"`
- **Line-range reading for large files:** `read_file(filename="log.txt", line_start=100, line_end=200)`
- **Path traversal protection:** All paths validated against `data/documents/` — no escape possible
- **Write verify:** Every written file is read back and length compared
- **Allowed write formats:** .txt, .md, .csv, .json, .xml, .html

### ChromaDB Management
- **Index:** Supports PDF, TXT, MD, CSV, DOCX, XLSX, PPTX, ODT, ODS, ODP
- **Chunking:** Automatic ~500-token chunks with overlap
- **Semantic search:** Embedding-based across all indexed documents
- **Central management:** `chromadb_stats` shows Research Cache, Documents and all Agent Memory collections at a glance

## Security

- All file operations confined to `data/documents/`
- Path traversal attempts (e.g. `../../etc/passwd`) are blocked
- Delete operations require WRITE_SYSTEM tier (highest before ADMIN)
- ChromaDB clear also requires WRITE_SYSTEM
