"""
Document Store - Chunking, Embedding & ChromaDB Storage for uploaded documents.

Uses the ChromaDB server and the embedding function from embeddings.py
(shared with agent memory), with its own collection for user documents.
"""

import asyncio
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from chromadb.errors import ChromaError, NotFoundError

from .config import (
    DEFAULT_OLLAMA_URL,
    DOCUMENT_CHUNK_OVERLAP,
    DOCUMENT_CHUNK_SIZE,
    DOCUMENT_COLLECTION,
    DOCUMENT_EMBED_BATCH_SIZE,
    DOCUMENT_SEARCH_DIVERSITY_WINDOW,
    DOCUMENT_SEARCH_NEIGHBOR_WINDOW,
    DOCUMENT_SEARCH_SEPARATE_CORPORA,
    DOCUMENTS_DIR,
)
from .document_search_index import DocumentSearchIndex
from .embeddings import OLLAMA_EMBEDDING_MODEL, OllamaEmbeddingFunction
from .logging_utils import log_message


def _read_text_file(file_path: Path) -> str:
    """Read a text file with automatic encoding detection via chardet."""
    raw = file_path.read_bytes()
    import chardet
    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"
    return raw.decode(encoding)


def _mmr_rerank(
    relevance: "np.ndarray",
    doc_embs: "np.ndarray",
    k: int,
    lambda_: float = 0.5,
) -> list[int]:
    """Greedy Maximal Marginal Relevance re-ranking.

    Spreads selected documents across the embedding space instead of
    returning many near-duplicate chunks. At each step picks the candidate
    with the highest score = ``λ·relevance(doc) − (1-λ)·max_sim(doc, selected)``.

    Args:
        relevance: How well each document answers the query, 1.0 = best (N items).
        doc_embs: Document embeddings (N items, each D-dim).
        k: How many documents to select (capped at N).
        lambda_: λ in [0,1]. 1.0 = pure relevance, 0.0 = pure diversity.
                 0.5 = balanced default.

    Returns:
        List of indices into ``doc_embs`` in selection order.
    """
    n = len(doc_embs)
    if n == 0:
        return []
    # Normalize once for cosine similarity
    embs_norm = doc_embs / (np.linalg.norm(doc_embs, axis=1, keepdims=True) + 1e-9)

    selected: list[int] = []
    remaining = set(range(n))
    target = min(k, n)
    while len(selected) < target and remaining:
        if not selected:
            best = max(remaining, key=lambda i: float(relevance[i]))
        else:
            sel_norm = embs_norm[selected]  # (M, D)
            best_score = -float("inf")
            best = next(iter(remaining))
            for i in remaining:
                # Max similarity of candidate to any already-selected doc
                redundancy = float(np.max(sel_norm @ embs_norm[i]))
                score = (
                    lambda_ * float(relevance[i])
                    - (1.0 - lambda_) * redundancy
                )
                if score > best_score:
                    best_score = score
                    best = i
        selected.append(best)
        remaining.discard(best)
    return selected


def _read_pdf(file_path: Path) -> str:
    """Extract text from a PDF using pdftotext (poppler-utils).

    pdftotext joins hyphenated line-breaks back into whole words and
    converts ligatures (ﬂ, ﬁ) to plain letters — both crucial for
    embedding quality. PyMuPDF preserves them as-is which results
    in fragmented embeddings (`Misch-` + `volk` as two halves, `Brotﬂ aden`
    as a strange unicode word).
    """
    import shutil
    import subprocess
    if not shutil.which("pdftotext"):
        raise RuntimeError(
            "pdftotext not installed. Please install poppler-utils:\n"
            "  Debian/Ubuntu:  sudo apt install poppler-utils\n"
            "  Fedora/RHEL:    sudo dnf install poppler-utils\n"
            "  macOS:          brew install poppler"
        )
    result = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(file_path), "-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    return result.stdout


def _read_csv(file_path: Path) -> str:
    """Read a CSV file and return as markdown table."""
    import csv
    import io
    content = _read_text_file(file_path)
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        return ""
    # Header + separator + data rows
    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    data = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
    return f"{header}\n{separator}\n{data}"


def _read_docx(file_path: Path) -> str:
    """Extract text from a .docx file."""
    from docx import Document
    doc = Document(str(file_path))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _read_xlsx(file_path: Path) -> str:
    """Extract text from a .xlsx file as markdown tables (one per sheet)."""
    from openpyxl import load_workbook
    wb = load_workbook(str(file_path), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        rows = [[str(cell) if cell is not None else "" for cell in row] for row in ws.iter_rows(values_only=True)]
        if not rows:
            continue
        header = "| " + " | ".join(rows[0]) + " |"
        separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
        data = "\n".join("| " + " | ".join(row) + " |" for row in rows[1:])
        parts.append(f"## {sheet}\n\n{header}\n{separator}\n{data}")
    wb.close()
    return "\n\n".join(parts)


def _read_pptx(file_path: Path) -> str:
    """Extract text from a .pptx file."""
    from pptx import Presentation
    prs = Presentation(str(file_path))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    if para.text.strip():
                        texts.append(para.text)
        if texts:
            parts.append(f"## Slide {i}\n\n" + "\n".join(texts))
    return "\n\n".join(parts)


def _read_odt(file_path: Path) -> str:
    """Extract text from a .odt file."""
    from odf.opendocument import load
    from odf.text import P
    doc = load(str(file_path))
    paragraphs = doc.getElementsByType(P)
    return "\n\n".join(
        "".join(str(node) for node in p.childNodes)
        for p in paragraphs
        if p.childNodes
    )


def _read_ods(file_path: Path) -> str:
    """Extract text from a .ods spreadsheet as markdown tables."""
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow
    from odf.text import P
    doc = load(str(file_path))
    parts: list[str] = []
    for table in doc.getElementsByType(Table):
        name = table.getAttribute("name") or "Sheet"
        rows: list[list[str]] = []
        for row in table.getElementsByType(TableRow):
            cells: list[str] = []
            for cell in row.getElementsByType(TableCell):
                text = "".join(
                    "".join(str(n) for n in p.childNodes)
                    for p in cell.getElementsByType(P)
                )
                cells.append(text)
            if any(c.strip() for c in cells):
                rows.append(cells)
        if not rows:
            continue
        # Normalize column count
        max_cols = max(len(r) for r in rows)
        rows = [r + [""] * (max_cols - len(r)) for r in rows]
        header = "| " + " | ".join(rows[0]) + " |"
        separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
        data = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
        parts.append(f"## {name}\n\n{header}\n{separator}\n{data}")
    return "\n\n".join(parts)


def _read_odp(file_path: Path) -> str:
    """Extract text from a .odp presentation."""
    from odf.draw import Page
    from odf.opendocument import load
    from odf.text import P
    doc = load(str(file_path))
    parts: list[str] = []
    for i, page in enumerate(doc.getElementsByType(Page), 1):
        texts: list[str] = []
        for p in page.getElementsByType(P):
            text = "".join(str(n) for n in p.childNodes)
            if text.strip():
                texts.append(text)
        if texts:
            parts.append(f"## Slide {i}\n\n" + "\n".join(texts))
    return "\n\n".join(parts)


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into chunks of *exactly* chunk_size tokens.

    Uses the Qwen3 tokenizer (already cached locally for token estimation
    elsewhere in the project) for true token-count accuracy. This replaces
    the old char-heuristic which under-estimated tokens for token-dense
    languages (German + Hebrew inserts) and could push embeddings past
    the model's context limit.

    Falls back to char-based slicing only if the tokenizer cannot be
    loaded — that path is safe but less accurate.
    """
    try:
        return _chunk_by_tokens(text, chunk_size, overlap)
    except Exception as exc:  # noqa: BLE001 — a document must not fail on its tokenizer
        log_message(f"⚠️ Tokenizer chunking failed ({exc}) — using char fallback")
        return _chunk_by_chars(text, chunk_size, overlap)


def _chunk_by_tokens(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Token-accurate chunker using the Qwen3 tokenizer.

    Uses ``encoding.offsets`` (char_start, char_end per token) to slice the
    chunk text directly out of the original string. This avoids BPE
    decode round-trips and the Unicode-replacement-char (``\\ufffd``)
    artifacts that appear when a chunk boundary lands inside a multi-
    byte rune — common with Hebrew + niqqud, where glyphs are encoded
    as multi-byte fallback tokens that the BPE vocabulary doesn't cover.
    """
    from .context_manager import _tokenizer_cache, count_tokens_with_tokenizer

    # Warm the cache via the public counter (handles download + caching)
    count_tokens_with_tokenizer("warmup")
    tokenizer = _tokenizer_cache.get("qwen3")
    if tokenizer is None:
        raise RuntimeError("Qwen3 tokenizer not initialised")

    encoding = tokenizer.encode(text)
    token_ids = encoding.ids
    offsets = encoding.offsets  # list[tuple[int, int]] — (char_start, char_end)
    if len(token_ids) <= chunk_size:
        stripped = text.strip()
        return [stripped] if stripped else []

    chunks: list[str] = []
    start = 0
    while start < len(token_ids):
        end = min(start + chunk_size, len(token_ids))
        char_start = offsets[start][0]
        char_end = offsets[end - 1][1]
        piece = text[char_start:char_end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(token_ids):
            break
        start = end - overlap
    return chunks


def _chunk_by_chars(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Char-based fallback. Less accurate, used only when tokenizer unavailable."""
    from .config import CHARS_PER_TOKEN
    chunk_chars = chunk_size * CHARS_PER_TOKEN
    overlap_chars = overlap * CHARS_PER_TOKEN

    if len(text) <= chunk_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_chars
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap_chars
    return chunks


PARSERS = {
    ".pdf": _read_pdf,
    ".txt": _read_text_file,
    ".md": _read_text_file,
    ".ics": _read_text_file,
    ".csv": _read_csv,
    ".docx": _read_docx,
    ".xlsx": _read_xlsx,
    ".pptx": _read_pptx,
    ".odt": _read_odt,
    ".ods": _read_ods,
    ".odp": _read_odp,
}

# Office/ODF container formats (ZIP with XML inside): their text exists only
# through the parser above. The workspace file tools (read_file,
# search_in_file) use it for these; plain-text formats they read as-is, so
# line numbers match the file.
CONTAINER_FORMATS = frozenset({".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"})


def document_folder(filename: str) -> str:
    """The folder a document counts to: the parent of its relative path, empty for the root."""
    return str(Path(filename).parent) if "/" in filename else ""


def chunk_id(filename: str, chunk_index: int) -> str:
    """The id of one passage. It carries the document's current name: the neighbours of a
    hit are looked up by it, and a later document of that name would overwrite it."""
    return f"{filename}__chunk_{chunk_index}"


def document_text(data: bytes, name: str) -> str:
    """The text of a file that exists only in memory, by the parsers of the index."""
    suffix = Path(name).suffix.lower()
    parser = PARSERS.get(suffix)
    if parser is None:
        raise ValueError(f"Unsupported file type: {name}. Readable: {', '.join(sorted(PARSERS))}")
    with tempfile.TemporaryDirectory(prefix="aifred-read-") as folder:
        copy = Path(folder) / f"document{suffix}"
        copy.write_bytes(data)
        return parser(copy)


class _ResilientCollection:
    """Drop-in replacement for direct collection access with stale-recovery.

    External callers reach the Chroma collection via ``store.collection``
    (this proxy) instead of ``store._collection`` (the raw handle).
    Each method call on the proxy forwards to the real collection; if
    Chroma raises ``NotFoundError`` (collection ID became stale, typically
    after a docker-auto-update recreate), the proxy reconnects the parent
    DocumentStore once and retries.

    The proxy is cheap: it holds only a reference to the parent store and
    creates wrappers per call — nothing is cached on the proxy itself.
    """

    __slots__ = ("_store",)

    def __init__(self, store: "DocumentStore") -> None:
        self._store = store

    def __getattr__(self, name: str) -> Any:
        # Resolve the attribute on the *current* underlying collection
        # each time; never cache it on the proxy because reconnect()
        # swaps out _store._collection.
        target = getattr(self._store._collection, name)
        if not callable(target):
            return target

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return target(*args, **kwargs)
            except NotFoundError:
                self._store.reconnect()
                # Re-resolve the bound method on the new collection
                fresh = getattr(self._store._collection, name)
                return fresh(*args, **kwargs)

        return _wrapped


class DocumentStore:
    """Manages document chunking, embedding and retrieval via ChromaDB."""

    def __init__(self, host: "str | None" = None, port: "int | None" = None):
        # None = config-Werte (CHROMA_HOST/CHROMA_PORT) — Factory-SSOT
        from .chroma_client import chroma_client
        self._client = chroma_client(host, port)
        self._client.heartbeat()
        # Two embedding functions for two access patterns:
        # - bulk indexing (many chunks at once) → GPU + 10 min keep_alive
        #   so the model stays warm for the duration of the upload
        # - single-shot query (one user question) → CPU, no GPU residency
        #   so we don't fight the active LLM for VRAM
        self._embed_index = OllamaEmbeddingFunction(
            model_name=OLLAMA_EMBEDDING_MODEL,
            host=DEFAULT_OLLAMA_URL,
            mode="index",
        )
        self._embed_query = OllamaEmbeddingFunction(
            model_name=OLLAMA_EMBEDDING_MODEL,
            host=DEFAULT_OLLAMA_URL,
            mode="query",
        )
        # Collection-default uses the query function — anything that doesn't
        # explicitly pass embeddings (only happens accidentally) stays on CPU.
        self._embed_fn = self._embed_query
        self._collection = self._resolve_collection()
        DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        log_message(f"📄 DocumentStore connected: {self._collection.count()} chunks")
        # Lazily-loaded set of distinct folder values for prefix-match search.
        # Invalidated whenever index_document / delete_document mutate the
        # collection — the cache stays correct without a per-search round-trip.
        self._folder_cache: set[str] | None = None
        # Read on the first search, dropped with every change. A thread lock, as the
        # store serves several event loops; it only guards the load.
        self._search_index: DocumentSearchIndex | None = None
        self._search_index_lock = threading.Lock()

    def _resolve_collection(self) -> Any:
        """Bootstrap-time get-or-create. Called ONLY from ``__init__`` —
        when the volume is fresh on first start the collection must exist
        afterwards, so create-if-missing is acceptable here.

        Do NOT reuse this for runtime reconnects: silently re-creating a
        collection during runtime hides real problems (e.g. an unexpected
        wipe) and orphans whatever data still sits in the volume under
        another collection-UUID.
        """
        return self._client.get_or_create_collection(
            name=DOCUMENT_COLLECTION,
            metadata={
                "description": "AIfred uploaded documents",
                "embedding_model": OLLAMA_EMBEDDING_MODEL,
            },
            embedding_function=self._embed_fn,  # type: ignore[arg-type]
        )

    def reconnect(self) -> None:
        """Refresh the cached collection handle WITHOUT creating one.

        Called when a runtime call hits ``NotFoundError`` — the server-side
        collection identity has changed (e.g. ChromaDB recreated the
        volume). We re-fetch by *name only*; if the named collection
        truly no longer exists, we raise loudly so the operator notices
        the data loss instead of silently producing an empty new one.
        """
        log_message("⚠️ DocumentStore: collection stale — reconnecting (get-only)")
        try:
            self._collection = self._client.get_collection(
                name=DOCUMENT_COLLECTION,
                embedding_function=self._embed_fn,  # type: ignore[arg-type]
            )
        except NotFoundError:
            log_message(
                f"❌ DocumentStore: collection '{DOCUMENT_COLLECTION}' missing on the "
                "server — refusing to silently re-create. The volume probably "
                "lost the collection metadata. Inspect chroma manually."
            )
            raise
        self._invalidate_caches()

    @property
    def collection(self) -> "_ResilientCollection":
        """Public, retry-on-stale collection accessor.

        Use this from external code (bibliothek_server, file_manager,
        bibliothek_search CLI) instead of `_collection` directly. Calls forward
        to the underlying ChromaDB collection; on NotFoundError the store
        reconnects once and retries.

        Internal callers (within DocumentStore) can still use `_collection`
        directly when they're certain the call is fresh — the store's own
        public methods wrap retry logic via _safe_call().
        """
        return _ResilientCollection(self)

    def _safe_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Run a collection method, reconnect once on NotFoundError.

        Used by DocumentStore.search/index/delete/etc. so the public API
        stays robust against stale cached collection IDs.
        """
        try:
            return getattr(self._collection, method_name)(*args, **kwargs)
        except NotFoundError:
            self.reconnect()
            return getattr(self._collection, method_name)(*args, **kwargs)

    def _get_known_folders(self) -> set[str]:
        if self._folder_cache is None:
            # Page through metadatas — Chroma's SQLite backend hits a
            # "too many SQL variables" wall around 32k rows otherwise.
            folders: set[str] = set()
            page_size = 5000
            offset = 0
            while True:
                data = self._collection.get(
                    include=["metadatas"], limit=page_size, offset=offset
                )
                metas = data.get("metadatas") or []
                if not metas:
                    break
                for m in metas:
                    if isinstance(m, dict):
                        folders.add(str(m.get("folder", "")))
                if len(metas) < page_size:
                    break
                offset += page_size
            self._folder_cache = folders
        return self._folder_cache

    def _invalidate_caches(self) -> None:
        self._folder_cache = None
        self._search_index = None

    def _current_search_index(self, count: int) -> DocumentSearchIndex:
        """The search index for what ChromaDB holds now. ``count`` catches what the
        invalidation cannot: a write while the load was running."""
        with self._search_index_lock:
            if self._search_index is None or len(self._search_index) != count:
                self._search_index = DocumentSearchIndex.load(self._collection)
            return self._search_index

    def _search_scope(
        self, index: DocumentSearchIndex, folder: str | None, without_corpora: bool
    ) -> "np.ndarray":
        """Which chunks a search covers. A named folder includes its sub-folders."""
        if folder is not None:
            return index.folder_mask(self._expand_folder_prefix(folder))
        corpora = [
            stored for corpus in DOCUMENT_SEARCH_SEPARATE_CORPORA if without_corpora
            for stored in self._expand_folder_prefix(corpus)
        ]
        return ~index.folder_mask(corpora)

    def _expand_folder_prefix(self, folder: str) -> list[str]:
        """Return all stored folder values that the requested folder covers.

        Exact match counts, plus any folder that lives below it. So
        ``"bibel"`` covers ``"bibel/Schlachter"`` and ``"bibel/GuteNachricht"``,
        ``"judaica"`` covers ``"judaica"`` itself plus ``"judaica/talmud"``,
        ``"judaica/midrash"`` etc.
        """
        prefix = folder + "/"
        return [
            f for f in self._get_known_folders()
            if f == folder or f.startswith(prefix)
        ]

    async def index_document(self, file_path: Path, filename: str, source: str = "") -> int:
        """Parse, chunk and embed a document. Returns number of chunks created.

        ``source`` is the key of a further document source the file lives in;
        empty for the local documents folder.

        All blocking calls (parser, embed, Chroma RPC) run on a thread so
        the asyncio event loop stays free for WebSocket heartbeats — large
        files can otherwise freeze the UI for many seconds and trigger
        client-side timeouts at the proxy.
        """
        suffix = file_path.suffix.lower()
        parser = PARSERS.get(suffix)
        if not parser:
            raise ValueError(f"Unsupported file type: {suffix}")

        text = await asyncio.to_thread(parser, file_path)
        if not text.strip():
            raise ValueError(f"Document is empty: {filename}")

        chunks = await asyncio.to_thread(
            _chunk_text, text, DOCUMENT_CHUNK_SIZE, DOCUMENT_CHUNK_OVERLAP
        )
        now = datetime.now().isoformat()

        folder = document_folder(filename)

        ids = [chunk_id(filename, i) for i in range(len(chunks))]
        metadatas = [
            {
                "filename": filename,
                "folder": folder,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "upload_date": now,
                "source": source,
                # Documents uploaded via UI are trusted; a source's files can change there.
                "source_trust": "external" if source else "trusted",
            }
            for i in range(len(chunks))
        ]

        # Delete any existing chunks for this filename first. Without this,
        # re-indexing a now-shorter file would leave zombie chunks in place
        # (old chunk_N where N > new total_chunks). With delete-before-upsert
        # the file always reflects the current source — no stale data.
        await asyncio.to_thread(self._collection.delete, where={"filename": filename})

        # Batched Embedding + Upsert.
        # Bei grossen Dokumenten (z.B. Schlachter-Bibel mit ~2686 Chunks) wuerde
        # ein einzelner Embedding-Call >90 s dauern. In der Zeit kommen keine
        # WS-Heartbeats durch → granian killt den Reflex-Worker als unresponsive
        # → Index-Loop tot. Mit Batches a 64 Chunks dauert jeder Call ~3-4 s und
        # der asyncio-Loop kann zwischendurch Heartbeats an den Browser senden.
        # Embed via the index-mode function (GPU + warm cache). Passing
        # the embeddings explicitly bypasses the collection's default
        # embedding_function (which is the query/CPU one) for this write.
        total = len(chunks)
        for i in range(0, total, DOCUMENT_EMBED_BATCH_SIZE):
            batch_end = min(i + DOCUMENT_EMBED_BATCH_SIZE, total)
            batch_chunks = chunks[i:batch_end]
            batch_ids = ids[i:batch_end]
            batch_metas = metadatas[i:batch_end]

            batch_emb = await asyncio.to_thread(self._embed_index, batch_chunks)
            await asyncio.to_thread(
                self._collection.upsert,
                ids=batch_ids,
                documents=batch_chunks,
                embeddings=batch_emb,
                metadatas=batch_metas,
            )
            log_message(
                f"📄 {filename}: embedded {batch_end}/{total} chunks "
                f"(+{len(batch_chunks)} in this batch)"
            )

        self._invalidate_caches()

        log_message(f"📄 Indexed {filename}: {total} chunks (in batches of {DOCUMENT_EMBED_BATCH_SIZE})")
        return total

    async def search(
        self,
        query: str,
        n_results: int = 5,
        folder: str | None = None,
        neighbor_window: int | None = None,
        page: int = 1,
        diversify: bool = True,
        mmr_lambda: float = 0.5,
        without_corpora: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int]:
        """Search every chunk by meaning and by words. Returns (hits, has_more, total_hits).

        Args:
            query: Search query.
            n_results: Page size — number of ranked hits per page.
            folder: If set, restrict search to this folder *or any folder
                    nested under it*. ``"bibel"`` covers ``"bibel/Schlachter"``
                    and ``"bibel/GuteNachricht"``; ``"bibel/Schlachter"``
                    stays narrow. ``None`` covers the whole store.
            neighbor_window: Per hit, also include ±N adjacent chunks of the
                    same document so the model sees the full surrounding
                    context (mid-sentence chunk cuts are mitigated).
                    None → use DOCUMENT_SEARCH_NEIGHBOR_WINDOW from config,
                    0 → off (ranked hits only).
            page: 1-based page number. ``page=1`` returns hits 1..n_results,
                  ``page=2`` returns hits n_results+1..2*n_results, etc.
            diversify: When True (default), apply MMR (Maximal Marginal Relevance)
                  to the best hits. Picks the most relevant hit, then each
                  subsequent hit by max ``λ·relevance − (1-λ)·max_redundancy``.
                  Spreads results across files / vector regions instead of
                  returning many near-duplicate chunks from one dominant document.
            mmr_lambda: λ in [0, 1]. 1.0 = pure relevance, 0.0 = pure diversity.
                  0.5 (default) = balanced.
            without_corpora: Leave out DOCUMENT_SEARCH_SEPARATE_CORPORA when no
                  folder is named. They have their own tools and would bury a
                  question about a document in scripture.

        Returns:
            (hits, has_more, total_hits): hits is the page's chunks (ranked + neighbors),
            has_more is True iff a further page holds hits, total_hits counts all hits.
        """
        count = await asyncio.to_thread(self._collection.count)
        if count == 0:
            return ([], False, 0)

        if neighbor_window is None:
            neighbor_window = DOCUMENT_SEARCH_NEIGHBOR_WINDOW

        page = max(1, page)
        skip = (page - 1) * n_results

        index = await asyncio.to_thread(self._current_search_index, count)
        # Embed the query via the CPU/query-mode function so we don't
        # wake up the GPU embedding model for a single-shot search.
        query_embeddings = await asyncio.to_thread(self._embed_query, [query])
        in_scope = await asyncio.to_thread(self._search_scope, index, folder, without_corpora)
        ranking = await asyncio.to_thread(index.rank, query, query_embeddings[0], in_scope)
        order = ranking.rows
        total_hits = len(order)

        # MMR orders the top of the ranking only: spread over everything, it would
        # bring up barely related chunks just for being different. The rest keeps its
        # place, so every hit stays reachable. Same query → same order → page-stable.
        if diversify and total_hits > 1:
            window = order[:DOCUMENT_SEARCH_DIVERSITY_WINDOW]
            relevance = ranking.scores[:len(window)] / ranking.scores[0]
            spread = _mmr_rerank(relevance, index.vectors(window), k=len(window), lambda_=mmr_lambda)
            order = np.concatenate((window[spread], order[len(window):]))

        page_rows = order[skip:skip + n_results]
        if len(page_rows) == 0:
            return ([], False, total_hits)
        has_more = (skip + n_results) < total_hits

        page_ids = [index.ids[row] for row in page_rows]
        stored = await asyncio.to_thread(
            self._collection.get, ids=page_ids, include=["documents", "metadatas"]
        )
        by_id = dict(zip(stored["ids"], zip(stored["documents"], stored["metadatas"], strict=True), strict=True))
        hits: list[dict[str, Any]] = []
        for row, passage_id in zip(page_rows, page_ids, strict=True):
            doc, meta = by_id[passage_id]
            hits.append({
                "content": doc,
                "filename": meta.get("filename", ""),
                "folder": meta.get("folder", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "total_chunks": meta.get("total_chunks", 0),
                "distance": float(ranking.distances[row]),
                "_neighbor": False,  # mark original ranked hits
            })

        # Fetch neighbor chunks for each ranked hit (deduped by ID).
        # Neighbors are added with _neighbor=True so callers can distinguish
        # ranked hits from contextual augmentation.
        if neighbor_window and neighbor_window > 0 and hits:
            existing_ids = {chunk_id(h["filename"], h["chunk_index"]) for h in hits}
            neighbor_ids: set[str] = set()
            for h in hits:
                fname = h["filename"]
                idx = h["chunk_index"]
                total = h.get("total_chunks", 0)
                for offset in range(-neighbor_window, neighbor_window + 1):
                    if offset == 0:
                        continue
                    nidx = idx + offset
                    if nidx < 0 or (total and nidx >= total):
                        continue
                    nid = chunk_id(fname, nidx)
                    if nid not in existing_ids:
                        neighbor_ids.add(nid)

            if neighbor_ids:
                neighbor_data = await asyncio.to_thread(
                    self._collection.get, ids=list(neighbor_ids)
                )
                if neighbor_data and neighbor_data.get("documents"):
                    for i, doc in enumerate(neighbor_data["documents"]):
                        meta = (neighbor_data["metadatas"][i]
                                if neighbor_data.get("metadatas") else {})  # type: ignore[index]
                        hits.append({
                            "content": doc,
                            "filename": meta.get("filename", ""),
                            "folder": meta.get("folder", ""),
                            "chunk_index": meta.get("chunk_index", 0),
                            "total_chunks": meta.get("total_chunks", 0),
                            "distance": None,  # not from similarity search
                            "_neighbor": True,
                        })

            # Sort by (filename, chunk_index) so consecutive chunks are
            # adjacent in the output — easier for the model to reconstruct
            # the original passage.
            hits.sort(key=lambda h: (h["filename"], h["chunk_index"]))

        return (hits, has_more, total_hits)

    def list_documents(self) -> list[dict[str, Any]]:
        """List all unique documents with metadata.

        Pages through metadatas (5k per call) — Chroma's SQLite backend
        hits "too many SQL variables" around 32k rows otherwise. Same
        pattern as ``_get_known_folders``.
        """
        if self._collection.count() == 0:
            return []

        docs: dict[str, dict[str, Any]] = {}
        page_size = 5000
        offset = 0
        while True:
            data = self._collection.get(
                include=["metadatas"], limit=page_size, offset=offset
            )
            metas = data.get("metadatas") or []
            if not metas:
                break
            for meta in metas:
                if not isinstance(meta, dict):
                    continue
                fname = str(meta.get("filename", ""))
                if fname and fname not in docs:
                    docs[fname] = {
                        "filename": fname,
                        "total_chunks": meta.get("total_chunks", 0),
                        "upload_date": meta.get("upload_date", ""),
                        "source": meta.get("source", ""),
                    }
            if len(metas) < page_size:
                break
            offset += page_size

        return list(docs.values())

    def clear(self) -> int:
        """Remove every indexed chunk (files on disk stay). Returns the count removed."""
        ids = self._collection.get(include=[])["ids"]
        if ids:
            self._collection.delete(ids=ids)
        self._invalidate_caches()
        return len(ids)

    async def delete_document(self, filename: str, delete_file: bool = True) -> int:
        """Delete all chunks for a document. Returns number of deleted chunks."""
        all_data = await asyncio.to_thread(
            self._collection.get, where={"filename": filename}
        )
        if not all_data or not all_data["ids"]:
            return 0

        count = len(all_data["ids"])
        await asyncio.to_thread(self._collection.delete, ids=all_data["ids"])
        self._invalidate_caches()

        # Delete file from disk if requested
        if delete_file:
            file_path = DOCUMENTS_DIR / filename
            if file_path.exists():
                file_path.unlink()

        log_message(f"🗑️ Deleted {filename}: {count} chunks")
        return count

    def rename_document(self, old_filename: str, new_filename: str) -> int:
        """Move a document to a new name, without embedding it again. What was indexed
        under the new name is replaced. Returns the number of chunks moved.

        The ids have to move too, see ``chunk_id``. The new ones are written before the
        old ones go, so a failure leaves the document findable."""
        data = self._collection.get(
            where={"filename": old_filename},
            include=["documents", "embeddings", "metadatas"],
        )
        old_ids = data["ids"]
        if not old_ids:
            return 0

        self._collection.delete(where={"filename": new_filename})
        metadatas = [
            {**meta, "filename": new_filename, "folder": document_folder(new_filename)}
            for meta in data["metadatas"]
        ]
        for start in range(0, len(old_ids), DOCUMENT_EMBED_BATCH_SIZE):
            end = start + DOCUMENT_EMBED_BATCH_SIZE
            batch = metadatas[start:end]
            self._collection.upsert(
                ids=[chunk_id(new_filename, meta["chunk_index"]) for meta in batch],
                documents=data["documents"][start:end],
                embeddings=data["embeddings"][start:end],
                metadatas=batch,
            )
        self._collection.delete(ids=old_ids)
        self._invalidate_caches()
        log_message(f"📄 Renamed {old_filename} → {new_filename}: {len(old_ids)} chunks")
        return len(old_ids)


# Singleton
_store: DocumentStore | None = None


def get_document_store() -> DocumentStore | None:
    """Get or create the global DocumentStore singleton."""
    global _store
    if _store is None:
        try:
            _store = DocumentStore()
        except (ChromaError, ConnectionError, OSError, ValueError) as e:
            log_message(f"❌ DocumentStore init failed: {e}")
            return None
    return _store
