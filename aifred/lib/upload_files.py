"""The files AIfred serves under ``/_upload/``: one table for every app that serves them (the Reflex
backend today, the server without Reflex after the switch, docs/de/architecture/vue-migration).

Every folder needs the login cookie (``AuthenticatedStaticFiles``), except the shared HTML previews:
a share link is meant to be opened without an AIfred login; its content is published by a deliberate
action of the user, and from outside the reverse proxy's login still protects it.
"""

from __future__ import annotations

from typing import NamedTuple

from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles

from .authenticated_static import AuthenticatedStaticFiles
from .config import DATA_DIR


class UploadFolder(NamedTuple):
    path: str            # URL path
    folder: str          # under data/
    name: str            # route name
    needs_login: bool


# The URL of the local documents (data/documents/): a file's URL is this + its path in the folder.
DOCUMENTS_URL = "/_upload/documents"

UPLOAD_FOLDERS = (
    # Uploads of the user (mobile camera, file picker).
    UploadFolder("/_upload/images", "upload/images", "uploaded_images", True),
    # Surveillance captures: snapshots of tool calls (toolcall/<session>/) and motion frames
    # (motion/<cam>/<date>/).
    UploadFolder("/_upload/vigilantia", "vigilantia", "vigilantia_images", True),
    # Shared chats (share_chat): opened by recipients without a login.
    UploadFolder("/_upload/html_preview", "html_preview", "html_preview", False),
    # Results of interactive code execution.
    UploadFolder("/_upload/sandbox_output", "sandbox_output", "sandbox_output", True),
    # Segments of a reading (24 h cleanup). The Echo reads them from disk, not through this path.
    UploadFolder("/_upload/tts_audio", "tts_audio", "tts_audio", True),
    # The audio a bubble keeps (replay); deleted with its session.
    UploadFolder("/_upload/audio", "audio", "session_audio", True),
    # One crop per identity for the "recognized persons" box of the live preview.
    UploadFolder("/_upload/face_crops", "vision/faces", "face_crops", True),
    # Documents of the document search (the most sensitive content).
    UploadFolder(DOCUMENTS_URL, "documents", "uploaded_documents", True),
)


def mount_upload_folders(app: Starlette) -> None:
    """Serve every folder of ``UPLOAD_FOLDERS`` on ``app`` (each folder is created if missing)."""
    for entry in UPLOAD_FOLDERS:
        directory = DATA_DIR / entry.folder
        directory.mkdir(parents=True, exist_ok=True)
        files = AuthenticatedStaticFiles if entry.needs_login else StaticFiles
        app.mount(entry.path, files(directory=str(directory)), name=entry.name)
