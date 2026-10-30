"""The Nextcloud Notes app as agents see it: a note is a text file in the notes
folder (``NEXTCLOUD_NOTES_FOLDER``), its title the file name, its category the
subfolder. Working on the files keeps the app and the agents on one state;
``AgentFiles`` keeps the folder a boundary.
"""

import re
from pathlib import PurePosixPath
from typing import Any

from .errors import NextcloudRefused
from .files import AgentFiles, clean_path

_NEW_NOTE_SUFFIX = ".md"
_NOTE_SUFFIXES = (".md", ".txt")
_NOT_IN_A_TITLE = re.compile(r'[*|/\\:"<>?]')  # the Notes app leaves them out of file names too


def _note_path(title: str, category: str | None) -> str:
    name = _NOT_IN_A_TITLE.sub(" ", title).strip()
    if not name:
        raise NextcloudRefused("a note needs a title")
    return clean_path(f"{category or ''}/{name}{_NEW_NOTE_SUFFIX}")


def _identity(path: str) -> dict[str, str]:
    note = PurePosixPath(path)
    return {"id": path, "title": note.stem, "category": "" if str(note.parent) == "." else str(note.parent)}


class Notes:
    def __init__(self, files: AgentFiles) -> None:
        self._files = files

    def search(self, query: str | None, limit: int) -> list[dict[str, Any]]:
        """By title, newest first; without a query the newest notes."""
        return [{**_identity(row["path"]), "modified": row["modified"]}
                for row in self._files.search(query or "", limit)
                if row["type"] == "file" and row["path"].endswith(_NOTE_SUFFIXES)]

    def get(self, note_id: str) -> dict[str, Any]:
        return {**_identity(note_id), "content": self._files.read(note_id)["content"]}

    def create(self, title: str, content: str, category: str | None) -> str:
        # Without the folder the share is missing: creating it here would hide
        # the note in AIfred's own account, where the user never sees it.
        if not self._files.folder_exists():
            raise NextcloudRefused("the notes folder is missing: it has to be shared with AIfred's Nextcloud user")
        path = _note_path(title, category)
        self._files.write(path, content, None)
        return path

    def update(self, note_id: str, changes: dict[str, Any]) -> str:
        """Returns the note's id, which follows a new title or category."""
        if "content" in changes:
            self._files.write(note_id, changes["content"], self._files.read(note_id)["etag"])
        current = _identity(note_id)
        target = _note_path(changes.get("title", current["title"]), changes.get("category", current["category"]))
        if "title" not in changes and "category" not in changes or target == note_id:
            return note_id
        return str(self._files.move(note_id, target)["path"])

    def delete(self, note_id: str) -> None:
        self._files.delete(note_id)
