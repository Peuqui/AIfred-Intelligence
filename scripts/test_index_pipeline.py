#!/usr/bin/env python3
"""Backend-Pipeline-Test: Indexiert eine kleine Datei direkt via DocumentStore.

Umgeht das Frontend komplett — verifiziert ob:
  - ChromaDB connection klappt
  - Ollama bge-m3 ladet
  - batched embedding (DOCUMENT_EMBED_BATCH_SIZE=64) sauber durchläuft

Run:
    venv/bin/python scripts/test_index_pipeline.py [rel_path]

Default rel_path: bibel/GuteNachricht/Bibel-GuteNachricht_notes.txt (~1 MB)
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aifred.lib.document_store import get_document_store  # noqa: E402

DOCUMENTS_DIR = REPO_ROOT / "data" / "documents"
DEFAULT_REL = "bibel/GuteNachricht/Bibel-GuteNachricht_notes.txt"


async def main() -> int:
    rel = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REL
    path = DOCUMENTS_DIR / rel
    if not path.exists():
        print(f"❌ File not found: {path}")
        return 1

    store = get_document_store()
    if store is None:
        print("❌ DocumentStore unavailable — is ChromaDB running?")
        return 1

    kb = path.stat().st_size / 1024
    print(f"📄 Indexing {rel} ({kb:.1f} KB)")
    print(f"   Collection: {store._collection.name}")
    print()

    t0 = time.time()
    chunks = await store.index_document(path, rel)
    elapsed = time.time() - t0
    print()
    print(f"✅ Done: {chunks} chunks in {elapsed:.1f}s "
          f"({chunks/max(elapsed,0.01):.1f} chunks/s)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
