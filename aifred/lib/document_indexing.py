"""Indexing documents as a server job (Vue documents page): files of the local documents folder and of
further sources (lib/document_sources.py).

Files wait in one queue and are indexed one after the other (``file_manager.index_file`` or
``index_source_file``: parser, chunks, bge-m3, ChromaDB) by one worker task in the server's event loop;
the page that queued them may close meanwhile. A file that fails is reported and the rest goes on.
``progress()`` is what a page shows: the file being indexed and how far the run is. A run lasts until
the queue is empty; files queued while it runs join it, and its outcome stays readable until the next
run starts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

from . import file_manager as fm
from .logging_utils import log_exception, log_message


@dataclass(frozen=True)
class IndexItem:
    """One file to index: a path below the local documents folder, or a file of a source."""

    label: str          # what the page shows: the relative path, or "<source>: <name>"
    source: str = ""    # "" = the local documents folder
    file_id: str = ""   # the file's id at its source
    name: str = ""      # the file's name at its source (its type decides the parser)


def local_item(rel_path: str) -> IndexItem:
    return IndexItem(label=rel_path)


def source_item(source: str, file_id: str, name: str) -> IndexItem:
    return IndexItem(label=f"{source}: {name}", source=source, file_id=file_id, name=name)


@dataclass(frozen=True)
class IndexFailure:
    path: str           # the item's label
    reason: str


@dataclass
class IndexProgress:
    running: bool = False
    current: str = ""   # the label of the file being indexed
    done: int = 0       # files of the run finished, indexed or failed
    total: int = 0      # files of the run, grows when more are queued
    indexed: int = 0
    chunks: int = 0     # sections the run put into the index
    failed: list[IndexFailure] = field(default_factory=list)


_progress = IndexProgress()
_waiting: list[IndexItem] = []
_current: IndexItem | None = None
_worker: asyncio.Task[None] | None = None


def progress() -> IndexProgress:
    """A copy of the current run's progress (or the last run's outcome)."""
    return replace(_progress, failed=list(_progress.failed))


def queue_files(items: list[IndexItem]) -> IndexProgress:
    """Queue files for indexing; one waiting or being indexed already is not queued twice. Starts
    the worker when none runs. Must be called in the server's event loop."""
    global _progress, _worker
    if not _progress.running:
        _progress = IndexProgress(running=True)
    new = [item for item in dict.fromkeys(items) if item not in _waiting and item != _current]
    _waiting.extend(new)
    _progress.total += len(new)
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_index_waiting())
    return progress()


async def _index(item: IndexItem) -> fm.FileOpResult:
    if item.source:
        return await fm.index_source_file(item.source, item.file_id, item.name)
    return await fm.index_file(item.label)


async def _index_waiting() -> None:
    global _current
    while _waiting:
        _current = _waiting.pop(0)
        _progress.current = _current.label
        try:
            result = await _index(_current)
        except Exception as error:  # noqa: BLE001 — one damaged file must not end the run; the page shows why
            log_exception(f"Indexing {_current.label} failed", error)
            result = fm.FileOpResult(False, str(error))
        _progress.done += 1
        if result.success:
            _progress.indexed += 1
            _progress.chunks += int(result.metadata["chunks"])
        else:
            _progress.failed.append(IndexFailure(_current.label, result.detail))
    log_message(
        f"📚 Index run: {_progress.indexed}/{_progress.total} files, {_progress.chunks} sections, "
        f"{len(_progress.failed)} failed",
    )
    _current = None
    _progress.current = ""
    _progress.running = False
