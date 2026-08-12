"""Workspace plugin — file access + ChromaDB document management.

Provides tools for:
- File system access: list, read, write files in data/documents/
- ChromaDB: index, search, list indexed, delete documents
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ....lib.config import (
    DOCUMENTS_DIR, DOCUMENT_SEARCH_MAX_RESULTS,
    DOCUMENT_SEARCH_DISTANCE_STRONG, WORKSPACE_READ_MAX_BYTES,
    CHROMA_HOST, CHROMA_PORT,
)
from ....lib import file_manager as fm
from ....lib.function_calling import Tool
from ....lib.security import TIER_READONLY, TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from ....lib.plugin_base import PluginContext, load_tool_description
from ....lib.i18n import t
from ....lib.logging_utils import log_message

# Base directory for all file operations (path traversal protection).
# ALLE Pfade in Tool-Parametern und -Antworten sind relativ hierzu.
_DOCUMENTS_DIR = DOCUMENTS_DIR


def _safe_resolve(relative_path: str) -> tuple[Path | None, str | None]:
    """Compatibility shim — delegates to the central file_manager.safe_resolve."""
    return fm.safe_resolve(relative_path)


@dataclass
class WorkspacePlugin:
    name: str = "workspace"
    display_name: str = "Workspace"
    description: str = "Lese-/Schreibzugriff aufs Arbeitsverzeichnis und semantische Dokument-Indexierung in ChromaDB (Vektor-Suche)."

    def is_available(self) -> bool:
        return True  # File access always available, ChromaDB optional

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        tools: list[Tool] = []

        # ============================================================
        # FILE SYSTEM TOOLS
        # ============================================================

        async def _list_files(subfolder: str = "") -> str:
            """List files in data/documents/ or a subfolder."""
            if subfolder:
                target, err = fm.safe_resolve(subfolder)
                if err or target is None:
                    return json.dumps({"error": "Access denied: path outside documents directory"})
            else:
                target = _DOCUMENTS_DIR.resolve()
            if not target.exists():
                return json.dumps({"error": f"Directory not found: {subfolder}"})

            # Pfad-Bezug ist IMMER die Dokumenten-Wurzel — genau das, was
            # read_file/write_file/translate_file als Parameter erwarten.
            # Vorher wurde relativ zu data/ gemeldet, also mit zusätzlichem
            # "documents/" davor: Ein list_files(subfolder="documents")
            # meldete "documents/documents", das Modell baute daraus
            # "documents/documents/<datei>" und lief in Endlosschleifen
            # aus File-not-found (beobachtet 2026-07-18).
            rel_dir = target.relative_to(_DOCUMENTS_DIR.resolve())
            rel_prefix = "" if str(rel_dir) == "." else f"{rel_dir}/"

            entries = []
            for item in sorted(target.iterdir()):
                entry: dict[str, Any] = {"name": item.name}
                # Fertiger Pfad für die Datei-Tools — erspart dem Modell
                # das fehleranfällige Zusammensetzen aus Ordner + Name.
                entry["path"] = f"{rel_prefix}{item.name}"
                if item.is_dir():
                    entry["type"] = "directory"
                    entry["items"] = len(list(item.iterdir()))
                else:
                    entry["type"] = "file"
                    entry["size_kb"] = round(item.stat().st_size / 1024, 1)
                    entry["extension"] = item.suffix.lower()
                entries.append(entry)

            log_message(f"📂 list_files: {rel_dir} ({len(entries)} entries)")
            return json.dumps(
                {"directory": str(rel_dir), "entries": entries},
                ensure_ascii=False,
            )

        tools.append(Tool(
            name="list_files",
            tier=TIER_READONLY,
            description=(
                load_tool_description(__file__, "list_files")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "subfolder": {
                        "type": "string",
                        "description": "Subfolder to list (empty = root of documents/)",
                        "default": "",
                    },
                },
            },
            executor=_list_files,
        ))

        async def _read_file(filename: str, pages: str = "", line_start: int | str = 0, line_end: int | str = 0) -> str:
            """Read a file from data/documents/. PDFs support page selection, text files support line ranges."""
            file_path, error = _safe_resolve(filename)
            if error:
                return json.dumps({"error": error})
            if not file_path or not file_path.exists():
                return json.dumps({"error": f"File not found: {filename}"})

            # Cap on file size: the whole file is loaded into RAM below, so a
            # very large file would blow the worker's memory. Point the model at
            # page/line ranges instead of failing opaquely.
            file_size = file_path.stat().st_size
            if file_size > WORKSPACE_READ_MAX_BYTES:
                return json.dumps({
                    "error": (
                        f"File too large ({round(file_size / 1024 / 1024, 1)} MB, "
                        f"limit {WORKSPACE_READ_MAX_BYTES // 1024 // 1024} MB). "
                        "Use 'pages' (PDF) or 'line_start'/'line_end' (text) to read a range."
                    )
                })

            log_message(f"📄 read_file: {file_path.name}")

            try:
                if file_path.suffix.lower() == ".pdf":
                    import fitz  # PyMuPDF
                    doc = fitz.open(str(file_path))
                    total_pages = len(doc)

                    if pages:
                        # Parse page range: "3", "1-5", "3,7,10-12"
                        selected: list[int] = []
                        for part in pages.split(","):
                            part = part.strip()
                            if "-" in part:
                                start, end = part.split("-", 1)
                                # Clamp to the actual page count BEFORE building the
                                # range — otherwise "1-2000000000" materializes a
                                # two-billion-element list and OOMs the worker.
                                start_i = max(1, int(start))
                                end_i = min(int(end), total_pages)
                                selected.extend(range(start_i - 1, end_i))
                            else:
                                selected.append(int(part) - 1)
                        selected = [p for p in selected if 0 <= p < total_pages]
                        text = "\n\n".join(
                            f"--- Page {p + 1} ---\n{doc[p].get_text()}" for p in selected
                        )
                    else:
                        text = "\n\n".join(
                            f"--- Page {i + 1} ---\n{page.get_text()}" for i, page in enumerate(doc)
                        )
                    doc.close()

                    log_message(f"  read_file: PDF {file_path.name} ({total_pages} pages, {len(text)} chars)")
                    return json.dumps({
                        "filename": file_path.name,
                        "type": "pdf",
                        "total_pages": total_pages,
                        "content": text,
                    }, ensure_ascii=False)
                else:
                    # Text-based files
                    try:
                        all_text = file_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        import chardet
                        raw = file_path.read_bytes()
                        detected = chardet.detect(raw)
                        all_text = raw.decode(str(detected.get("encoding") or "utf-8"), errors="replace")

                    lines = all_text.split("\n")
                    total_lines = len(lines)

                    # Apply line range if specified
                    ls = int(line_start)
                    le = int(line_end)
                    if ls > 0 or le > 0:
                        start_idx = max(0, ls - 1)  # 1-based to 0-based
                        end_idx = le if le > 0 else total_lines
                        text = "\n".join(lines[start_idx:end_idx])
                        range_info = f"lines {start_idx + 1}-{min(end_idx, total_lines)} of {total_lines}"
                    else:
                        text = all_text
                        range_info = f"all {total_lines} lines"

                    log_message(f"  read_file: {file_path.name} ({range_info}, {len(text)} chars)")
                    return json.dumps({
                        "filename": file_path.name,
                        "type": file_path.suffix.lower().lstrip("."),
                        "total_lines": total_lines,
                        "range": range_info,
                        "size_kb": round(file_path.stat().st_size / 1024, 1),
                        "content": text,
                    }, ensure_ascii=False)
            except Exception as e:
                log_message(f"  read_file failed: {e}")
                return json.dumps({"error": f"Cannot read {filename}: {e}"})

        tools.append(Tool(
            name="read_file",
            tier=TIER_READONLY,
            description=(
                load_tool_description(__file__, "read_file")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Filename relative to documents/ (e.g. 'report.pdf')",
                    },
                    "pages": {
                        "type": "string",
                        "description": "Page range for PDFs: '3', '1-5', '3,7,10-12' (empty = all pages)",
                        "default": "",
                    },
                    "line_start": {
                        "type": "integer",
                        "description": "First line to read (1-based). Use with line_end for large text files.",
                        "default": 0,
                    },
                    "line_end": {
                        "type": "integer",
                        "description": "Last line to read (inclusive). 0 = until end of file.",
                        "default": 0,
                    },
                },
                "required": ["filename"],
            },
            executor=_read_file,
        ))

        async def _write_file(filename: str, content: str) -> str:
            """Write or overwrite a text file in data/documents/."""
            file_path, error = _safe_resolve(filename)
            if error:
                return json.dumps({"error": error})
            if not file_path:
                return json.dumps({"error": f"Invalid path: {filename}"})

            # Only allow text-based writes
            allowed_extensions = {".txt", ".md", ".csv", ".json", ".xml", ".html"}
            if file_path.suffix.lower() not in allowed_extensions:
                return json.dumps({
                    "error": f"Cannot write {file_path.suffix} files. Allowed: {', '.join(sorted(allowed_extensions))}"
                })

            # Create parent dirs if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)

            file_path.write_text(content, encoding="utf-8")

            # Verify: read back and compare length
            written_text = file_path.read_text(encoding="utf-8")
            if len(written_text) != len(content):
                return json.dumps({
                    "error": f"Verify failed: wrote {len(content)} chars but read back {len(written_text)}"
                })

            size_kb = round(file_path.stat().st_size / 1024, 1)
            log_message(f"  write_file: {file_path.name} ({size_kb} KB, verified)")
            result_json = json.dumps({
                "written": filename,
                "size_kb": size_kb,
                "chars": len(content),
                "verified": True,
            })
            # HTML-Artefakte in derselben Chat-Bubble einbetten wie
            # execute_code_write das tut — die Pipeline parst
            # `SANDBOX_HTML_URL:` Zeilen aus dem Tool-Result und baut daraus
            # ein iframe. URL geht über den vorhandenen /_upload/documents/
            # static mount, kein Kopiervorgang nötig.
            if file_path.suffix.lower() in {".html", ".htm"}:
                # file_path is resolved (via _safe_resolve); use the resolved
                # base too, else a symlink in DATA_DIR makes relative_to raise.
                rel = file_path.relative_to(_DOCUMENTS_DIR.resolve()).as_posix()
                return (
                    f"{result_json}\n\n"
                    f"SANDBOX_HTML_URL: /_upload/documents/{rel}\n\n"
                    "The HTML file is automatically embedded in the chat. "
                    "Do NOT paste the code as a markdown block. "
                    "Just describe what was built."
                )
            return result_json

        tools.append(Tool(
            name="write_file",
            tier=TIER_WRITE_DATA,
            description=(
                load_tool_description(__file__, "write_file")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Filename relative to documents/ (e.g. 'notes/summary.md')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write",
                    },
                },
                "required": ["filename", "content"],
            },
            executor=_write_file,
        ))

        async def _create_folder(folder_name: str) -> str:
            """Create a subfolder in data/documents/."""
            # Split into parent + leaf so file_manager.create_folder can
            # validate the leaf name and resolve the parent independently.
            parts = folder_name.strip("/").rsplit("/", 1)
            parent_rel, leaf = ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
            result = fm.create_folder(parent_rel, leaf)
            if not result.success:
                return json.dumps({"error": result.detail})
            return json.dumps({"created": folder_name, "path": result.metadata.get("path", folder_name)})

        tools.append(Tool(
            name="create_folder",
            tier=TIER_WRITE_DATA,
            description=load_tool_description(__file__, "create_folder"),
            parameters={
                "type": "object",
                "properties": {
                    "folder_name": {
                        "type": "string",
                        "description": "Folder path relative to documents/ (e.g. 'projects/2026')",
                    },
                },
                "required": ["folder_name"],
            },
            executor=_create_folder,
        ))

        async def _delete_file(filename: str) -> str:
            """Delete a file from data/documents/ (also removes from index if present)."""
            parts = filename.strip("/").rsplit("/", 1)
            parent_rel, leaf = ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
            # Capture size before delete for the JSON output
            path, _ = fm.safe_resolve(filename)
            size_kb = round(path.stat().st_size / 1024, 1) if path and path.is_file() else 0
            result = await fm.delete_file(parent_rel, leaf, from_disk=True, from_index=True)
            if not result.success:
                return json.dumps({"error": result.detail})
            return json.dumps({
                "deleted": filename,
                "size_kb": size_kb,
                "chunks_removed": result.metadata.get("chunks_removed", 0),
            })

        tools.append(Tool(
            name="delete_file",
            tier=TIER_WRITE_SYSTEM,
            description=load_tool_description(__file__, "delete_file"),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Filename relative to documents/ (e.g. 'old_notes.txt')",
                    },
                },
                "required": ["filename"],
            },
            executor=_delete_file,
        ))

        async def _delete_folder(folder_name: str, recursive: bool = False) -> str:
            """Delete a folder. Empty by default; ``recursive=True`` to wipe contents too."""
            parts = folder_name.strip("/").rsplit("/", 1)
            parent_rel, leaf = ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
            result = await fm.delete_folder(parent_rel, leaf, recursive=recursive, from_index=True)
            if not result.success:
                return json.dumps({"error": result.detail})
            return json.dumps({
                "deleted": folder_name,
                "recursive": recursive,
                "files_removed": result.metadata.get("files_removed", 0),
                "subfolders_removed": result.metadata.get("subfolders_removed", 0),
                "chunks_removed": result.metadata.get("chunks_removed", 0),
            })

        tools.append(Tool(
            name="delete_folder",
            tier=TIER_WRITE_SYSTEM,
            description=(
                load_tool_description(__file__, "delete_folder")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "folder_name": {
                        "type": "string",
                        "description": "Folder path relative to documents/ (e.g. 'old_project')",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, deletes folder and all contents (files + subfolders) recursively.",
                        "default": False,
                    },
                },
                "required": ["folder_name"],
            },
            executor=_delete_folder,
        ))

        async def _copy_file(path: str, target_path: str, overwrite: bool = False) -> str:
            """Copy a file (binary-safe, server-side) inside the documents tree."""
            result = fm.copy_file(path, target_path, overwrite=overwrite)
            if not result.success:
                return json.dumps({"error": result.detail})
            return json.dumps({
                "copied": path,
                "to": result.metadata.get("path", target_path),
                "bytes": result.metadata.get("bytes", 0),
            })

        tools.append(Tool(
            name="copy_file",
            tier=TIER_WRITE_DATA,
            description=(
                load_tool_description(__file__, "copy_file")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Source file relative to documents/ (e.g. 'notes/draft.md')",
                    },
                    "target_path": {
                        "type": "string",
                        "description": "Target path — a file path or an existing folder (file keeps its name)",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Replace an existing target (default false)",
                    },
                },
                "required": ["path", "target_path"],
            },
            executor=_copy_file,
        ))

        async def _move_file(path: str, target_path: str, overwrite: bool = False) -> str:
            """Move a file or folder; index metadata follows moved files."""
            result = fm.move_file(path, target_path, overwrite=overwrite)
            if not result.success:
                return json.dumps({"error": result.detail})
            return json.dumps({
                "moved": path,
                "to": result.metadata.get("path", target_path),
                "chunks_updated": result.metadata.get("chunks_updated", 0),
            })

        tools.append(Tool(
            name="move_file",
            tier=TIER_WRITE_DATA,
            description=(
                load_tool_description(__file__, "move_file")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Source file or folder relative to documents/",
                    },
                    "target_path": {
                        "type": "string",
                        "description": "Target path — a new path or an existing folder (source keeps its name)",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Replace an existing target (default false)",
                    },
                },
                "required": ["path", "target_path"],
            },
            executor=_move_file,
        ))

        async def _rename(path: str, new_name: str) -> str:
            """Rename a file or folder. Updates the ChromaDB index for indexed files."""
            parts = path.strip("/").rsplit("/", 1)
            parent_rel, leaf = ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
            result = fm.rename(parent_rel, leaf, new_name, sync_index=True)
            if not result.success:
                return json.dumps({"error": result.detail})
            return json.dumps({
                "renamed": path,
                "to": new_name,
                "chunks_updated": result.metadata.get("chunks_updated", 0),
            })

        tools.append(Tool(
            name="rename",
            tier=TIER_WRITE_DATA,
            description=(
                load_tool_description(__file__, "rename")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Current path relative to documents/ (e.g. 'notes/draft.md')",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "New name (just the filename or folder name, not a full path)",
                    },
                },
                "required": ["path", "new_name"],
            },
            executor=_rename,
        ))

        # ============================================================
        # CHROMA DB TOOLS
        # ============================================================

        async def _index_document(filename: str) -> str:
            """Index a file from data/documents/ into ChromaDB."""
            # Extension-Check bleibt hier, weil nur das index-Tool ihn braucht.
            file_path, error = fm.safe_resolve(filename)
            if error:
                return json.dumps({"error": error})
            if not file_path or not file_path.exists():
                return json.dumps({"error": f"File not found: {filename}"})
            from ....lib.config import DOCUMENT_ALLOWED_EXTENSIONS
            if file_path.suffix.lower() not in DOCUMENT_ALLOWED_EXTENSIONS:
                return json.dumps({
                    "error": f"Unsupported file type: {file_path.suffix}. "
                    f"Allowed: {', '.join(sorted(DOCUMENT_ALLOWED_EXTENSIONS))}"
                })
            try:
                result = await fm.index_file(filename)
            except Exception as e:
                return json.dumps({"error": f"Indexing failed: {e}"})
            if not result.success:
                return json.dumps({"error": result.detail})
            return json.dumps({
                "indexed": file_path.name,
                "chunks": result.metadata.get("chunks", 0),
            })

        tools.append(Tool(
            name="index_document",
            tier=TIER_WRITE_DATA,
            description=(
                load_tool_description(__file__, "index_document")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Filename in documents/ to index (e.g. 'report.pdf')",
                    },
                },
                "required": ["filename"],
            },
            executor=_index_document,
        ))

        async def _search_documents(
            query: str,
            n_results: int = 5,
            folder: Optional[str] = None,
            page: int = 1,
        ) -> str:
            """Semantic search in ChromaDB, optionally restricted to a folder.

            ``page`` enables pagination: page=2 returns hits ``n_results+1``
            through ``2*n_results`` of the same similarity ranking. Use this
            when the topic is broad (many relevant passages exist) and the
            first page didn't surface enough material.
            """
            page = max(1, int(page or 1))
            result = await fm.search_index(
                query, n_results=n_results, folder=folder, page=page
            )
            if not result.success:
                return json.dumps({"error": result.detail})
            hits = result.metadata.get("results", [])
            has_more = bool(result.metadata.get("has_more"))
            if not hits:
                # Bei page > 1 ist das Ende der Pagination — kein Hinweis
                # auf leeren Index, sondern nur "weiter gibt's nichts mehr".
                if page > 1:
                    return json.dumps({
                        "results": [],
                        "page": page,
                        "has_more": False,
                        "message": (
                            f"No more results on page {page}. The previous "
                            f"page(s) covered the available similarity hits "
                            f"for this query — try refining the query."
                        ),
                    })
                # Erst checken ob die Collection überhaupt was enthält —
                # bei leerer DB ist jeder Search-Call hoffnungslos und
                # der LLM würde sonst in einer Suchbegriff-Schleife landen.
                # Dem Aufrufer sofort signalisieren: hör auf zu suchen,
                # ruf list_indexed() auf oder index erst.
                from ....lib.file_manager import list_indexed
                idx_result = list_indexed()
                indexed_count = len(idx_result.metadata.get("documents", []))
                if indexed_count == 0:
                    return json.dumps({
                        "results": [],
                        "message": (
                            "STOP — the document index is EMPTY (0 documents). "
                            "Searching with different queries will NOT help. "
                            "Either index documents first (index_document) or "
                            "tell the user that no knowledge base is available."
                        ),
                        "indexed_count": 0,
                    })
                # Folder explizit nicht repräsentiert → klare Liste was es gibt
                if folder:
                    folders_in_index = sorted({
                        d.get("folder", "") for d in idx_result.metadata.get("documents", [])
                    })
                    return json.dumps({
                        "results": [],
                        "message": (
                            f"No matches in folder '{folder}'. The index has "
                            f"{indexed_count} documents in these folders: "
                            f"{folders_in_index}. Try a different folder or "
                            f"omit the parameter to search the whole index."
                        ),
                        "indexed_count": indexed_count,
                        "available_folders": folders_in_index,
                    })
                return json.dumps({
                    "results": [],
                    "message": (
                        f"No matches for this query. The index has "
                        f"{indexed_count} documents — try different keywords "
                        f"or list_indexed() to see what's available."
                    ),
                    "indexed_count": indexed_count,
                })
            # Tag each hit with a qualitative relevance label derived from
            # its L2 distance — the model can act on "high"/"medium" but
            # not on a raw distance whose scale it cannot interpret.
            # Neighbor chunks carry no distance → "context".
            results = []
            strong_hits = 0
            similarity_hits = 0
            for hit in hits:
                dist = hit.get("distance")
                if hit.get("_neighbor") or dist is None:
                    relevance = "context"
                else:
                    similarity_hits += 1
                    if dist < DOCUMENT_SEARCH_DISTANCE_STRONG:
                        relevance = "high"
                        strong_hits += 1
                    else:
                        relevance = "medium"
                results.append({
                    "filename": hit["filename"],
                    "folder": hit.get("folder", ""),
                    "chunk": f"{hit['chunk_index'] + 1}/{hit['total_chunks']}",
                    "content": hit["content"],
                    "relevance": relevance,
                })
            payload: dict[str, Any] = {
                "total_results": len(results),
                "page": page,
                "has_more": has_more,
                "results": results,
            }
            # Pagination guidance: only invite a next page while the current
            # one is still mostly strong hits. Once a page is dominated by
            # weak matches, deeper pages only get worse — withhold the
            # invitation and tell the model to stop instead.
            if has_more and similarity_hits and strong_hits * 2 >= similarity_hits:
                payload["next_page_hint"] = (
                    f"Page {page} still has strong matches — call page={page + 1} "
                    f"with the same query for more. Stop once the topic is covered."
                )
            elif has_more:
                payload["pagination_note"] = (
                    f"Page {page} hits are only weakly related and further pages "
                    f"will not be better. Stop paginating; refine the query or "
                    f"work with what you have."
                )
            return json.dumps(payload, ensure_ascii=False)

        tools.append(Tool(
            name="search_documents",
            tier=TIER_READONLY,
            description=(
                load_tool_description(__file__, "search_documents")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — what are you looking for?",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": f"Page size — number of similarity hits per page (default: 5, max: {DOCUMENT_SEARCH_MAX_RESULTS})",
                        "default": 5,
                    },
                    "folder": {
                        "type": "string",
                        "description": (
                            "Folder to restrict the search to. Includes all "
                            "nested sub-folders automatically — pass 'bibel' "
                            "to search both Schlachter and GuteNachricht, "
                            "'judaica' to search everything Jewish, "
                            "'bibel/Schlachter' to narrow to one translation. "
                            "Omit to search across all indexed content."
                        ),
                    },
                    "page": {
                        "type": "integer",
                        "description": (
                            "1-based page number for pagination. page=1 (default) "
                            "returns the top similarity hits, page=2 the next batch "
                            "deeper into the ranking, etc. Use the SAME query when "
                            "paginating. Watch has_more in the response to decide "
                            "whether another page is worth fetching."
                        ),
                        "default": 1,
                    },
                },
                "required": ["query"],
            },
            executor=_search_documents,
        ))

        async def _list_indexed() -> str:
            """List all documents indexed in ChromaDB."""
            result = fm.list_indexed()
            if not result.success:
                return json.dumps({"error": result.detail})
            docs = result.metadata.get("documents", [])
            if not docs:
                return json.dumps({"documents": [], "message": "No documents indexed yet."})
            return json.dumps({"total_count": len(docs), "documents": docs}, ensure_ascii=False)

        tools.append(Tool(
            name="list_indexed",
            tier=TIER_READONLY,
            description=(
                load_tool_description(__file__, "list_indexed")
            ),
            parameters={"type": "object", "properties": {}},
            executor=_list_indexed,
        ))

        async def _list_orphaned() -> str:
            """List indexed documents whose source file is missing on disk."""
            result = fm.list_orphaned()
            if not result.success:
                return json.dumps({"error": result.detail})
            orphans = result.metadata.get("orphans", [])
            total = result.metadata.get("total_indexed", 0)
            if not orphans:
                return json.dumps({
                    "orphans": [],
                    "total_indexed": total,
                    "message": "No orphans found — every indexed document still has its source file.",
                })
            return json.dumps({
                "total_indexed": total,
                "orphan_count": len(orphans),
                "orphans": orphans,
            }, ensure_ascii=False)

        tools.append(Tool(
            name="list_orphaned",
            tier=TIER_READONLY,
            description=(
                load_tool_description(__file__, "list_orphaned")
            ),
            parameters={"type": "object", "properties": {}},
            executor=_list_orphaned,
        ))

        # ============================================================
        # CHROMADB ADMIN TOOLS
        # ============================================================

        async def _chromadb_stats() -> str:
            """Show all ChromaDB collections with entry counts."""
            try:
                import chromadb
                from chromadb.config import Settings
                client = chromadb.HttpClient(
                    host=CHROMA_HOST, port=CHROMA_PORT,
                    settings=Settings(anonymized_telemetry=False),
                )
                client.heartbeat()
            except Exception as e:
                return json.dumps({"error": f"ChromaDB not reachable: {e}"})

            collections = client.list_collections()
            result = []
            for col in collections:
                count = col.count()
                # Get sample metadata for context
                sample = col.peek(limit=1)
                metadatas = sample.get("metadatas") if sample else None
                meta_keys = list(metadatas[0].keys()) if metadatas and len(metadatas) > 0 else []  # type: ignore[index]
                result.append({
                    "name": col.name,
                    "entries": count,
                    "metadata_fields": meta_keys,
                })

            log_message(f"🗄️ chromadb_stats: {len(result)} collections")
            return json.dumps({
                "total_collections": len(result),
                "collections": result,
            }, ensure_ascii=False)

        tools.append(Tool(
            name="chromadb_stats",
            tier=TIER_READONLY,
            description=(
                load_tool_description(__file__, "chromadb_stats")
            ),
            parameters={"type": "object", "properties": {}},
            executor=_chromadb_stats,
        ))

        async def _chromadb_clear(collection_name: str, confirm: bool = False) -> str:
            """Clear all entries from a ChromaDB collection."""
            if not confirm:
                # Destructive, irreversible bulk delete — require an explicit
                # confirm flag so a hallucinated/injected call can't wipe a
                # collection in one step. The model must ask the user first.
                return json.dumps({
                    "error": "confirmation required",
                    "hint": (
                        "This irreversibly deletes ALL entries of "
                        f"'{collection_name}'. Ask the user for confirmation, "
                        "then call again with confirm=true."
                    ),
                })
            try:
                import chromadb
                from chromadb.config import Settings
                client = chromadb.HttpClient(
                    host=CHROMA_HOST, port=CHROMA_PORT,
                    settings=Settings(anonymized_telemetry=False),
                )
                col = client.get_collection(collection_name)
            except Exception as e:
                return json.dumps({"error": f"Collection '{collection_name}' not found: {e}"})

            count = col.count()
            if count == 0:
                return json.dumps({"collection": collection_name, "message": "Already empty"})

            all_ids = col.get(include=[])["ids"]
            col.delete(ids=all_ids)

            log_message(f"🗑️ chromadb_clear: {collection_name} ({count} entries removed)")
            return json.dumps({
                "cleared": collection_name,
                "entries_removed": count,
            })

        tools.append(Tool(
            name="chromadb_clear",
            tier=TIER_WRITE_SYSTEM,
            description=(
                load_tool_description(__file__, "chromadb_clear")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "collection_name": {
                        "type": "string",
                        "description": "Exact collection name (e.g. 'research_cache', 'aifred_documents')",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true. Only set after the user explicitly confirmed the irreversible deletion.",
                    },
                },
                "required": ["collection_name", "confirm"],
            },
            executor=_chromadb_clear,
        ))

        return tools

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "list_files":
            sub = tool_args.get("subfolder", "")
            return f"📂 {sub or 'documents/'}"
        elif tool_name == "read_file":
            return f"📄 {tool_args.get('filename', '')}"
        elif tool_name == "write_file":
            return f"📝 {tool_args.get('filename', '')}"
        elif tool_name == "create_folder":
            return f"📁 {tool_args.get('folder_name', '')}"
        elif tool_name == "delete_file":
            return f"🗑️ {tool_args.get('filename', '')}"
        elif tool_name == "delete_folder":
            return f"🗑️ {tool_args.get('folder_name', '')}"
        elif tool_name == "index_document":
            return f"📥 {tool_args.get('filename', '')}"
        elif tool_name == "search_documents":
            query = tool_args.get("query", "")
            return f"🔍 {query[:50]}" if query else t("tool_doc_search", lang=lang)
        elif tool_name == "list_indexed":
            return t("tool_doc_list", lang=lang)
        elif tool_name == "chromadb_stats":
            return "🗄️ ChromaDB"
        elif tool_name == "chromadb_clear":
            return f"🗑️ {tool_args.get('collection_name', 'ChromaDB')}"
        return ""


plugin = WorkspacePlugin()
