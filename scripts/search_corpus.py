#!/usr/bin/env python3
"""Search the AIfred Vector-DB corpus from the command line.

Two modes:
- **semantic** (default): bge-m3 vector search, like the LLM agents see it.
  Folder prefix-match (`--folder bibel` covers `bibel/Schlachter` and
  `bibel/GuteNachricht`). Includes ±neighbor chunks per hit.
- **literal** (``--grep``): exact substring match across all chunk texts.
  Useful when you want to verify a precise wording (e.g. checking
  whether a quoted phrase actually appears in a translation).

Examples:
    venv/bin/python scripts/search_corpus.py "Heiliger Geist" \\
        --folder bibel --n 10
    venv/bin/python scripts/search_corpus.py --grep "ewigen Gericht verfallen"
    venv/bin/python scripts/search_corpus.py --grep "Ruach Hakodesh" \\
        --folder judaica/kommentare

Pipe-friendly with --json for tooling integration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aifred.lib.document_store import get_document_store  # noqa: E402


def _truncate(text: str, max_chars: int) -> str:
    """Collapse whitespace and trim to max_chars with a … marker."""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1].rstrip() + "…"


def _highlight(text: str, needle: str, before: int = 80, after: int = 200) -> str:
    """Return a context window around the first occurrence of needle."""
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return _truncate(text, before + after)
    start = max(0, idx - before)
    end = min(len(text), idx + len(needle) + after)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return " ".join(snippet.split())


async def semantic_search(
    query: str, folder: str | None, n_results: int, neighbor: int,
) -> list[dict[str, Any]]:
    store = get_document_store()
    if store is None:
        raise SystemExit("DocumentStore unavailable — is ChromaDB running?")
    return await store.search(
        query=query, n_results=n_results, folder=folder, neighbor_window=neighbor,
    )


def literal_search(
    needle: str, folder: str | None, max_results: int,
) -> list[dict[str, Any]]:
    store = get_document_store()
    if store is None:
        raise SystemExit("DocumentStore unavailable — is ChromaDB running?")

    where: dict[str, Any] | None = None
    if folder is not None:
        matching = store._expand_folder_prefix(folder)
        if matching:
            where = (
                {"folder": matching[0]} if len(matching) == 1
                else {"folder": {"$in": matching}}
            )
        else:
            where = {"folder": folder}

    # Page through to avoid the SQLite parameter wall on large collections.
    # Normalise whitespace on both sides — chunks come from PDFs with hard
    # line breaks ("ewigen\nGericht verfallen") that a user query won't have.
    needle_norm = " ".join(needle.lower().split())
    hits: list[dict[str, Any]] = []
    page = 5000
    offset = 0
    while True:
        kwargs: dict[str, Any] = {
            "include": ["documents", "metadatas"],
            "limit": page,
            "offset": offset,
        }
        if where is not None:
            kwargs["where"] = where
        data = store.collection.get(**kwargs)
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        if not docs:
            break
        for doc, meta in zip(docs, metas):
            if not isinstance(doc, str):
                continue
            doc_norm = " ".join(doc.lower().split())
            if needle_norm not in doc_norm:
                continue
            hits.append({
                "filename": (meta or {}).get("filename", ""),
                "chunk_index": (meta or {}).get("chunk_index", 0),
                "total_chunks": (meta or {}).get("total_chunks", 0),
                "folder": (meta or {}).get("folder", ""),
                "content": doc,
            })
            if len(hits) >= max_results:
                return hits
        if len(docs) < page:
            break
        offset += page
    return hits


def format_hit(hit: dict[str, Any], idx: int, needle: str | None) -> str:
    fname = hit.get("filename", "")
    chunk_idx = hit.get("chunk_index", 0)
    total = hit.get("total_chunks", 0)
    distance = hit.get("distance")
    is_neighbor = hit.get("_neighbor", False)
    content = hit.get("content", "")

    header = f"[{idx}] {fname} chunk {chunk_idx}/{total}"
    if distance is not None:
        header += f"  (distance={distance:.3f})"
    if is_neighbor:
        header += "  ±neighbor"

    if needle is not None:
        body = _highlight(content, needle)
    else:
        body = _truncate(content, 350)

    indented = textwrap.fill(body, width=92, initial_indent="    ",
                             subsequent_indent="    ")
    return f"{header}\n{indented}"


def main() -> int:
    p = argparse.ArgumentParser(
        prog="search_corpus",
        description="Search the AIfred vector DB (semantic or literal).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              search_corpus "Heiliger Geist" --folder bibel --n 10
              search_corpus --grep "ewigen Gericht verfallen"
              search_corpus --grep "Ruach Hakodesh" --folder judaica/kommentare
        """),
    )
    p.add_argument("query", nargs="?", default=None,
                   help="Semantic-search query (omit when --grep is used)")
    p.add_argument("--grep", "-g", metavar="STRING", default=None,
                   help="Literal-substring search instead of semantic")
    p.add_argument("--folder", "-f", default=None,
                   help="Restrict to folder (prefix-match: 'bibel' covers "
                        "'bibel/Schlachter' + 'bibel/GuteNachricht')")
    p.add_argument("--n", "-n", type=int, default=10,
                   help="Max results (default: 10)")
    p.add_argument("--neighbor", type=int, default=1,
                   help="±neighbor chunks per semantic hit (default: 1, "
                        "matches what the LLM agents see)")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of formatted text")
    args = p.parse_args()

    if args.grep is None and args.query is None:
        p.error("either provide a query or use --grep STRING")
    if args.grep is not None and args.query is not None:
        p.error("provide either a semantic query or --grep, not both")

    if args.grep is not None:
        hits = literal_search(args.grep, args.folder, args.n)
        needle = args.grep
        mode = "literal"
    else:
        hits = asyncio.run(
            semantic_search(args.query, args.folder, args.n, args.neighbor)
        )
        needle = None
        mode = "semantic"

    if args.json:
        print(json.dumps({
            "mode": mode,
            "query": args.query if mode == "semantic" else args.grep,
            "folder": args.folder,
            "total": len(hits),
            "results": hits,
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"# {mode} search — "
          f"{'query=' + repr(args.query) if mode == 'semantic' else 'grep=' + repr(args.grep)}"
          f"{' folder=' + repr(args.folder) if args.folder else ''}")
    print(f"# {len(hits)} hits\n")

    for i, hit in enumerate(hits, start=1):
        print(format_hit(hit, i, needle))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
