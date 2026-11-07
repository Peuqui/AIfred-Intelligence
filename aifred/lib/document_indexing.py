"""Indexing files of the local documents folder as a server job (Vue documents page).

Files wait in one queue and are indexed one after the other (``file_manager.index_file``: parser,
chunks, bge-m3, ChromaDB) by one worker task in the server's event loop; the page that queued them
may close meanwhile. A file that fails is reported and the rest goes on. ``progress()`` is what a
page shows: the file being indexed and how far the run is. A run lasts until the queue is empty;
files queued while it runs join it, and its outcome stays readable until the next run starts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

from . import file_manager as fm
from .logging_utils import log_exception, log_message


@dataclass(frozen=True)
class IndexFailure:
    path: str
    reason: str


@dataclass
class IndexProgress:
    running: bool = False
    current: str = ""   # the file being indexed (relative to the documents folder)
    done: int = 0       # files of the run finished, indexed or failed
    total: int = 0      # files of the run, grows when more are queued
    indexed: int = 0
    chunks: int = 0     # sections the run put into the index
    failed: list[IndexFailure] = field(default_factory=list)


_progress = IndexProgress()
_waiting: list[str] = []
_worker: asyncio.Task[None] | None = None


def progress() -> IndexProgress:
    """A copy of the current run's progress (or the last run's outcome)."""
    return replace(_progress, failed=list(_progress.failed))


def queue_files(rel_paths: list[str]) -> IndexProgress:
    """Queue files for indexing; one waiting or being indexed already is not queued twice. Starts
    the worker when none runs. Must be called in the server's event loop."""
    global _progress, _worker
    if not _progress.running:
        _progress = IndexProgress(running=True)
    new = [path for path in dict.fromkeys(rel_paths) if path not in _waiting and path != _progress.current]
    _waiting.extend(new)
    _progress.total += len(new)
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_index_waiting())
    return progress()


async def _index_waiting() -> None:
    while _waiting:
        path = _progress.current = _waiting.pop(0)
        try:
            result = await fm.index_file(path)
        except Exception as error:  # noqa: BLE001 — one damaged file must not end the run; the page shows why
            log_exception(f"Indexing {path} failed", error)
            result = fm.FileOpResult(False, str(error))
        _progress.done += 1
        if result.success:
            _progress.indexed += 1
            _progress.chunks += int(result.metadata["chunks"])
        else:
            _progress.failed.append(IndexFailure(path, result.detail))
    log_message(
        f"📚 Index run: {_progress.indexed}/{_progress.total} files, {_progress.chunks} sections, "
        f"{len(_progress.failed)} failed",
    )
    _progress.current = ""
    _progress.running = False
