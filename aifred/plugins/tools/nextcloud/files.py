"""The Nextcloud folder the agents work in (``NEXTCLOUD_AGENT_FOLDER``).

Agents may read, write, move and delete everything inside it, subfolders
included, and nothing outside. They name paths relative to the folder, and
:func:`clean_path` refuses every way out of it. The folder itself is the
boundary: they may fill it but not delete or move it. The document manager
(``source.py``) does not go through here: the user sees the whole account.
"""

import mimetypes
from typing import Any

from ....lib.line_window import LineWindow
from .dav import DavClient, DavConflict, DavError, DavFile
from .errors import NextcloudRefused

_NOT_FOUND = 404
_TEXT = "text/plain"
SEARCH_LIMIT_MAX = 200


def clean_path(value: str) -> str:
    """A relative path without empty segments. "." and ".." never pass, nor do backslashes and control
    characters: Nextcloud has no symlinks, so a path that stays below its folder cannot leave it."""
    parts = [part for part in value.strip().split("/") if part]
    for part in parts:
        if part in (".", "..") or "\\" in part or any(ord(char) < 32 for char in part):
            raise NextcloudRefused(f"{value!r} is no path inside the folder")
    return "/".join(parts)


def _row(entry: DavFile, path: str) -> dict[str, Any]:
    return {"path": path, "type": "folder" if entry.is_folder else "file", "size": entry.size,
            "modified": entry.modified, "mime": entry.mime}


def _content_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or _TEXT


class AgentFiles:
    def __init__(self, client: DavClient, folder: str) -> None:
        self._client = client
        self._folder = folder

    def _inside(self, path: str) -> str:
        return f"{self._folder}/{path}".strip("/")

    def _item(self, path: str) -> str:
        """A file or folder an agent may change: strictly inside, the agent folder itself excluded."""
        cleaned = clean_path(path)
        if not cleaned:
            raise NextcloudRefused(f"the agent folder {self._folder!r} itself cannot be changed, moved or deleted")
        return self._inside(cleaned)

    def _relative(self, entry: DavFile) -> str:
        return entry.path.removeprefix(f"{self._folder}/")

    def _make_folders(self, path: str) -> None:
        """``path`` and the folders above it, the agent folder included."""
        parts = path.split("/")
        for depth in range(1, len(parts) + 1):
            self._client.make_folder("/".join(parts[:depth]))

    def folder_exists(self) -> bool:
        try:
            self._client.list_files(self._folder)
        except DavError as exc:
            if exc.status != _NOT_FOUND:
                raise
            return False
        return True

    def listing(self, folder: str) -> list[dict[str, Any]]:
        """A folder's content; the agent folder is empty until something is written there."""
        path = clean_path(folder)
        try:
            entries = self._client.list_files(self._inside(path))
        except DavError as exc:
            if exc.status != _NOT_FOUND:
                raise
            if path:
                raise NextcloudRefused(f"no folder {path!r}") from exc
            return []
        return [_row(entry, self._relative(entry)) for entry in entries]

    def search(self, term: str, limit: int) -> list[dict[str, Any]]:
        """Files and folders with ``term`` in their name."""
        try:
            entries = self._client.search_files(self._folder, term, max(1, min(limit, SEARCH_LIMIT_MAX)))
        except DavError as exc:
            if exc.status != _NOT_FOUND:
                raise
            return []
        return [_row(entry, self._relative(entry)) for entry in entries]

    def read(self, path: str, line_start: int = 0, line_end: int = 0) -> dict[str, Any]:
        """The text, or a range of its lines. Length and range stand before the
        content: the tool output is cut from the end, and they must survive that."""
        cleaned = clean_path(path)
        window = LineWindow(line_start, line_end)
        try:
            etag = self._client.read_file(self._item(cleaned), window.feed)
            part, total_lines, range_info = window.result()
        except UnicodeDecodeError as exc:
            raise NextcloudRefused(f"{cleaned} is no text file") from exc
        except DavError as exc:
            if exc.status != _NOT_FOUND:
                raise
            raise NextcloudRefused(f"no file {cleaned!r}") from exc
        return {"path": cleaned, "etag": etag, "total_lines": total_lines, "range": range_info, "content": part}

    def write(self, path: str, content: str, etag: str | None) -> dict[str, Any]:
        """A new file, or with the ``etag`` from reading it an overwrite if it is unchanged."""
        cleaned = clean_path(path)
        target = self._item(cleaned)
        if not etag:
            self._make_folders(target.rsplit("/", 1)[0])
        try:
            new_etag = self._client.write_file(target, content.encode("utf-8"), _content_type(target), etag)
        except DavConflict as exc:
            if etag:
                raise NextcloudRefused(f"{cleaned} was changed elsewhere in the meantime; read it again") from exc
            raise NextcloudRefused(f"{cleaned} exists already; read it and pass its etag to overwrite it") from exc
        return {"path": cleaned, "etag": new_etag}

    def move(self, path: str, target: str) -> dict[str, Any]:
        """Move or rename inside the agent folder; an existing target stays."""
        source, destination = self._item(path), self._item(target)
        self._make_folders(destination.rsplit("/", 1)[0])
        try:
            self._client.move_file(source, destination)
        except DavConflict as exc:
            raise NextcloudRefused(f"{clean_path(target)} exists already") from exc
        except DavError as exc:
            if exc.status != _NOT_FOUND:
                raise
            raise NextcloudRefused(f"no file {clean_path(path)!r}") from exc
        return {"path": clean_path(target)}

    def delete(self, path: str) -> None:
        """Goes to the trash bin of the plugin's user."""
        target = self._item(path)
        try:
            self._client.delete_file(target)
        except DavError as exc:
            if exc.status != _NOT_FOUND:
                raise
            raise NextcloudRefused(f"no file {clean_path(path)!r}") from exc
