"""The areas the file requester can open (lib/api/files.py): each a root and the actions allowed in it.

The server decides both; a client names only the area, so no page can open an arbitrary root. A new place that needs
the requester gets an entry here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .document_sources import DocumentSource, get_document_source
from .file_ops import AreaOps, DocumentOps, FolderOps
from .source_ops import SourceOps, source_actions
from .user_error import Missing, Refused

# The actions of the requester, named as VueFinder names its features (newfolder, upload …); "symlink" is AIfred's.
ACTIONS = frozenset({"newfolder", "rename", "delete", "upload", "download", "preview", "search", "copy", "move", "symlink"})


@dataclass(frozen=True)
class FileArea:
    root: Callable[[], str]     # what the requester shows as the place: the folder, for a source its name; read on every call
    actions: frozenset[str]
    ops: type[FolderOps] = field(default=FolderOps)   # what the actions do there (DocumentOps: the index follows)

    def work(self) -> AreaOps:
        return self.ops(self.root())

    def require(self, action: str) -> None:
        if action not in self.actions:
            raise Refused("file_action_not_allowed", action=action)


@dataclass(frozen=True)
class SourceArea(FileArea):
    """A document source (Nextcloud, Google Drive …) as an area: ``source-<key>``. Its place is the source's name, its
    actions what the source says it can (lib/source_ops.py), the work is the source's."""

    source: DocumentSource = field(kw_only=True)

    def work(self) -> AreaOps:
        return SourceOps(self.source)


def _voices_root() -> str:
    from .tts_client import get_tts_client

    return str(get_tts_client().voices_dir())


def _documents_root() -> str:
    from .config import DOCUMENTS_DIR

    return str(DOCUMENTS_DIR)


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
    # The documents folder of the search (data/documents/): every change through file_manager, so the index follows;
    # no links (an indexed path through a link would lead elsewhere than the file the index names).
    "documents": FileArea(_documents_root, ACTIONS - {"symlink"}, DocumentOps),
}


# A session's sandbox outputs (plots, pages, screenshots the agents made in it): "sandbox-<session id>", only for the
# session's owner. Looked at, downloaded, renamed, removed; made only by the sandbox.
SANDBOX_PREFIX = "sandbox-"
# A further document source (lib/document_sources.py) of an enabled plugin: "source-<key>", for every user as the
# documents page offers it. What it may do, the source says itself.
SOURCE_PREFIX = "source-"
SANDBOX_ACTIONS = frozenset({"rename", "delete", "download", "preview", "search"})


def file_area(name: str, user: str) -> FileArea:
    """The area ``name`` for ``user``; a session's area only when the session is theirs (404 otherwise, as for a
    session of someone else)."""
    if name.startswith(SANDBOX_PREFIX):
        return _session_sandbox(name.removeprefix(SANDBOX_PREFIX), user)
    if name.startswith(SOURCE_PREFIX):
        return _document_source(name.removeprefix(SOURCE_PREFIX))
    area = AREAS.get(name)
    if area is None:
        raise Missing("no_file_area", area=name)
    return area


def _document_source(key: str) -> FileArea:
    """The source with this key; 409 when its plugin is off, 404 when unknown or not connected (as the documents page)."""
    from .prompt_loader import get_language

    source = get_document_source(key)
    return SourceArea(lambda: source.label(get_language()), source_actions(source), source=source)


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
