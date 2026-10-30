"""Further document sources for the document manager — beside the local
documents folder, which the core handles itself.

A tool plugin offers one through an optional attribute ``document_source``
(an object satisfying :class:`DocumentSource`, or None while switched off).
The core discovers it among the enabled plugins and never imports a plugin:
a disabled plugin simply offers no source. Folders are addressed by id (""
= the source's root), since a remote store need not have paths.

A source's file can join the document index (``file_manager.index_source_file``):
the user decides per file or folder, as with local documents. The file stays at
the source; the index keeps its text under :func:`source_index_name`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class DocumentSourceError(RuntimeError):
    """A source could not do what was asked (not connected, not found, too
    large …); the message is meant for the user. Sources raise only this."""


@dataclass(frozen=True)
class SourceEntry:
    """One file or folder of a source."""

    id: str
    name: str
    is_folder: bool
    size_bytes: int | None   # None: folders and documents without a stored size
    modified: str            # ISO timestamp as the source reports it
    mime: str


@dataclass(frozen=True)
class SourceDownload:
    """A file ready to stream: name and type as it should arrive at the user."""

    name: str
    mime: str
    chunks: AsyncIterator[bytes]


@runtime_checkable
class DocumentSource(Protocol):
    key: str          # stable id, e.g. "google_drive"
    writable: bool    # False: the document manager offers no upload, upload() refuses

    def label(self, lang: str) -> str: ...  # its name in the document manager

    async def list_folder(self, folder_id: str) -> list[SourceEntry]: ...

    async def search(self, query: str) -> list[SourceEntry]: ...

    async def download(self, file_id: str) -> SourceDownload: ...

    async def upload(self, folder_id: str, name: str, data: bytes, mime: str) -> SourceEntry: ...


def source_index_name(source_key: str, file_id: str) -> str:
    """A source's file in the document index. The index derives its folders from
    this name, so the folder ``<key>:`` covers everything from one source."""
    return f"{source_key}:/{file_id}"


async def files_below(source: DocumentSource, folder_id: str) -> list[SourceEntry]:
    """Every file in the folder and its subfolders."""
    files: list[SourceEntry] = []
    for entry in await source.list_folder(folder_id):
        if entry.is_folder:
            files += await files_below(source, entry.id)
        else:
            files.append(entry)
    return files


def document_sources() -> dict[str, DocumentSource]:
    """The sources the enabled, available plugins offer right now, by key."""
    from .plugin_registry import discover_tools

    sources: dict[str, DocumentSource] = {}
    for plugin in discover_tools():
        source = getattr(plugin, "document_source", None)
        if source is not None and plugin.is_available():
            sources[source.key] = source
    return sources


def get_document_source(key: str) -> DocumentSource:
    """The source with this key; ``LookupError`` when no enabled plugin offers it."""
    source = document_sources().get(key)
    if source is None:
        raise LookupError(f"no document source {key!r} (plugin disabled or not connected)")
    return source
