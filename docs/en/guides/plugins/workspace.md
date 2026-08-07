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
| `rename` | Rename a file or folder (keeps the ChromaDB index in sync) | WRITE_DATA |
| `copy_file` | Copy a file (server-side, binary-safe — MP3/PDF too) | WRITE_DATA |
| `move_file` | Move a file or folder (index follows moved files) | WRITE_DATA |
| `delete_file` | Delete a file (also removes it from the index) | WRITE_SYSTEM |
| `delete_folder` | Delete a folder (empty by default, `recursive=true` to wipe the tree) | WRITE_SYSTEM |

### ChromaDB (Vector Database)

| Tool | Description | Tier |
|------|------------|------|
| `index_document` | Index a file into ChromaDB (chunking + embedding) | WRITE_DATA |
| `search_documents` | Search indexed documents semantically (folder filter, pagination, MMR) | READONLY |
| `list_indexed` | List all indexed documents | READONLY |
| `list_orphaned` | List indexed documents whose source file is missing on disk | READONLY |
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
- **Chunking:** Automatic ~800-token chunks with overlap
- **Semantic search:** Embedding-based across all indexed documents
- **Folder filter:** `search_documents(folder="bibel")` restricts the search and automatically includes all nested sub-folders
- **Relevance labels:** Each hit is tagged `high` / `medium` (similarity) or `context` (neighbour chunks added around a hit)
- **MMR diversification:** Results are spread across files/vector regions instead of returning many near-duplicate chunks
- **Pagination:** Re-run the same query with `page=2`, `page=3`, … — the response carries `has_more` plus a `next_page_hint` or `pagination_note` telling you whether deeper pages are worthwhile
- **Orphan cleanup:** `list_orphaned` finds index entries whose source file was deleted on disk
- **Central management:** `chromadb_stats` shows Research Cache, Documents and all Agent Memory collections at a glance

## Security

- All file operations confined to `data/documents/`
- Path traversal attempts (e.g. `../../etc/passwd`) are blocked
- Delete operations require WRITE_SYSTEM tier (highest before ADMIN)
- ChromaDB clear also requires WRITE_SYSTEM
