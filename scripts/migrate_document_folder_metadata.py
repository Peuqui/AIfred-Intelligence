#!/usr/bin/env python3
"""
Migrate existing Document-Store chunks to include the new `folder` metadata.

Adds a `folder` field to every chunk in the document_store collection by
deriving it from the existing `filename` metadata (parent path). Uses
collection.update() — embeddings are NOT recomputed, only metadata changes.

Idempotent: chunks that already have a `folder` field are skipped.

Run from the repo root:
    venv/bin/python scripts/migrate_document_folder_metadata.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a script from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aifred.lib.config import DOCUMENT_COLLECTION  # noqa: E402
import chromadb  # noqa: E402

CHROMA_HOST = "localhost"
CHROMA_PORT = 8000


def main() -> int:
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    try:
        collection = client.get_collection(DOCUMENT_COLLECTION)
    except Exception as exc:
        print(f"Collection '{DOCUMENT_COLLECTION}' not found: {exc}")
        return 1

    total = collection.count()
    if total == 0:
        print(f"Collection '{DOCUMENT_COLLECTION}' is empty — nothing to migrate.")
        return 0

    print(f"Scanning {total} chunks in '{DOCUMENT_COLLECTION}'…")
    data = collection.get()
    ids = data.get("ids") or []
    metadatas = data.get("metadatas") or []

    updated_ids: list[str] = []
    updated_metas: list[dict] = []
    skipped = 0

    for chunk_id, meta in zip(ids, metadatas):
        if not isinstance(meta, dict):
            continue
        if "folder" in meta:
            skipped += 1
            continue

        filename = str(meta.get("filename", ""))
        folder = str(Path(filename).parent) if "/" in filename else ""

        new_meta = dict(meta)
        new_meta["folder"] = folder
        updated_ids.append(chunk_id)
        updated_metas.append(new_meta)

    if not updated_ids:
        print(f"All {total} chunks already have `folder` metadata. Nothing to do.")
        return 0

    print(f"Updating {len(updated_ids)} chunks (skipped {skipped} already-migrated)…")
    # ChromaDB allows metadata-only update — embeddings stay intact
    collection.update(ids=updated_ids, metadatas=updated_metas)  # type: ignore[arg-type]

    print("Done. Sample of new metadata:")
    sample = collection.get(ids=updated_ids[:3])
    for cid, meta in zip(sample.get("ids") or [], sample.get("metadatas") or []):
        print(f"  {cid} → folder='{meta.get('folder')}'  filename='{meta.get('filename')}'")

    return 0


if __name__ == "__main__":
    sys.exit(main())
