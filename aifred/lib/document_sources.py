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

from .user_error import Missing, UpstreamFailed


class DocumentSourceError(UpstreamFailed):
    """A source could not do what was asked (not connected, not found, too
    large …), worded for the user (502). Sources raise only this."""


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


@runtime_checkable
class EditableDocumentSource(DocumentSource, Protocol):
    """A source that can also change what it holds. The source says so by having these methods (the file
    requester offers the matching actions only then, and only while ``writable``); no list of capable sources
    lives in the core. Every method answers with the id the item has afterwards (an id that is a path changes
    with a rename or move), which the search index follows."""

    async def create_folder(self, folder_id: str, name: str) -> str: ...

    async def rename(self, file_id: str, name: str) -> str: ...

    async def delete(self, file_id: str) -> None: ...

    async def move(self, file_id: str, target_folder_id: str) -> str: ...

    async def copy(self, file_id: str, target_folder_id: str) -> str: ...


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


def require_known_document_source(key: str) -> str:
    """The plugin (name) whose source has this key, on or off; ``Missing`` (404) when no plugin has one. What is
    left of a source in the index (taking it out) needs only this, not the source itself."""
    from .plugin_registry import document_source_owners

    owner = document_source_owners().get(key)
    if owner is None:
        raise Missing("no_document_source", source=key)
    return owner


def get_document_source(key: str) -> DocumentSource:
    """The source with this key. ``Conflict`` (409) when its plugin is off, ``Missing`` (404, a ``LookupError``)
    when it is unknown or the plugin offers none right now (not connected)."""
    source = document_sources().get(key)
    if source is None:
        from .plugin_registry import require_plugin_enabled

        require_plugin_enabled(require_known_document_source(key))
        raise Missing("no_document_source", source=key)
    return source
