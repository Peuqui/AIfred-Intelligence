#!/usr/bin/env python3
"""Re-index all Judaica .txt files into the AIfred vector DB.

The ``DocumentStore`` does delete-before-upsert per filename, so a
re-run cleanly overwrites previous chunks for files whose content
changed (e.g. after the Hebrew-pairing rewrite, or after adding new
Tanakh books).

Embedder: bge-m3 in GPU index-mode (configured in
aifred/lib/vector_cache.py + config.EMBEDDING_USE_GPU). Thanks to
the recent ``asyncio.to_thread`` fix in document_store.py, the event
loop stays free during long index runs.

Run:
    venv/bin/python scripts/reindex_judaica.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aifred.lib.document_store import get_document_store  # noqa: E402

JUDAICA_ROOT = REPO_ROOT / "data" / "documents" / "judaica"
DOCUMENTS_DIR = REPO_ROOT / "data" / "documents"


async def main() -> int:
    store = get_document_store()
    if store is None:
        print("DocumentStore unavailable — is ChromaDB running?")
        return 1

    files = sorted(JUDAICA_ROOT.rglob("*.txt"))
    # Skip INDEX.md and any non-corpus files
    files = [f for f in files if f.name != "INDEX.md"]
    print(f"Found {len(files)} judaica files to (re-)index")
    print("Embedder: GPU index-mode, bge-m3")
    print()

    start = time.time()
    total_chunks = 0
    failures: list[tuple[str, str]] = []

    for i, path in enumerate(files, start=1):
        rel = path.relative_to(DOCUMENTS_DIR).as_posix()
        kb = path.stat().st_size / 1024
        t0 = time.time()
        try:
            chunks = await store.index_document(path, rel)
            elapsed = time.time() - t0
            total_chunks += chunks
            print(f"[{i:2d}/{len(files)}] {rel:55s}  "
                  f"{kb:7.1f} KB  {chunks:4d} chunks  {elapsed:5.1f}s")
        except Exception as exc:
            print(f"[{i:2d}/{len(files)}] {rel}  FAIL: {exc}")
            failures.append((rel, str(exc)))

    elapsed = time.time() - start
    print()
    print(f"Done: {len(files) - len(failures)}/{len(files)} indexed, "
          f"{total_chunks} chunks total in {elapsed/60:.1f} min")
    if failures:
        print("Failures:")
        for rel, msg in failures:
            print(f"  - {rel}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
