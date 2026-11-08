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


def _voices_root() -> str:
    from .tts_client import get_tts_client

    return str(get_tts_client().voices_dir())


def _audio_root() -> str:
    from .audio_library import picker_root

    return picker_root()


# The audio sources: the sandbox the audio player's sources are chosen in (audio settings), curated in place —
# folders, links to folders of the sandbox, files moved, uploaded or removed.
AREAS: dict[str, FileArea] = {
    "audio": FileArea(_audio_root, ACTIONS),
    # The TTS-Suite's voices folder (its /config): a folder per voice with its samples and transcript. No links: the
    # suite reads the folder from inside its container, a link to a path of this machine would not resolve there.
    "voices": FileArea(_voices_root, ACTIONS - {"symlink"}),
}


# A session's sandbox outputs (plots, pages, screenshots the agents made in it): "sandbox-<session id>", only for the
# session's owner. Looked at, downloaded, renamed, removed; made only by the sandbox.
SANDBOX_PREFIX = "sandbox-"
SANDBOX_ACTIONS = frozenset({"rename", "delete", "download", "preview", "search"})


def file_area(name: str, user: str) -> FileArea:
    """The area ``name`` for ``user``; a session's area only when the session is theirs (404 otherwise, as for a
    session of someone else)."""
    if name.startswith(SANDBOX_PREFIX):
        return _session_sandbox(name.removeprefix(SANDBOX_PREFIX), user)
    area = AREAS.get(name)
    if area is None:
        raise Missing("no_file_area", area=name)
    return area


def _session_sandbox(session_id: str, user: str) -> FileArea:
    from .sandbox import session_output_dir
    from .session_storage import load_session

    try:
        session = load_session(session_id)
    except ValueError:            # no session id (32 hex characters)
        session = None
    if session is None or str(session.get("owner", "")).lower() != user:
        raise Missing("no_session")
    folder = str(session_output_dir(session_id))
    return FileArea(lambda: folder, SANDBOX_ACTIONS)
