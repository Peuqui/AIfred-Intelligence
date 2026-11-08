"""The areas the file requester can open (lib/api/files.py): each a root and the actions allowed in it.

The server decides both; a client names only the area, so no page can open an arbitrary root. A new place that needs
the requester gets an entry here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .user_error import Missing, Refused

# The actions of the requester, named as VueFinder names its features (newfolder, upload …); "symlink" is AIfred's.
ACTIONS = frozenset({"newfolder", "rename", "delete", "upload", "download", "preview", "search", "copy", "move", "symlink"})


@dataclass(frozen=True)
class FileArea:
    root: Callable[[], str]     # read on every call: a setting may move it
    actions: frozenset[str]

    def require(self, action: str) -> None:
        if action not in self.actions:
            raise Refused("file_action_not_allowed", action=action)


def _audio_root() -> str:
    from .audio_library import picker_root

    return picker_root()


# The audio sources: the sandbox the audio player's sources are chosen in (audio settings), curated in place —
# folders, links to folders of the sandbox, files moved, uploaded or removed.
AREAS: dict[str, FileArea] = {
    "audio": FileArea(_audio_root, ACTIONS),
}


def file_area(name: str) -> FileArea:
    area = AREAS.get(name)
    if area is None:
        raise Missing("no_file_area", area=name)
    return area
